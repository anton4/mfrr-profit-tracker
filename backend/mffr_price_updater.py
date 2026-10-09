import requests
import sqlite_utils
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
import time
import os

import entsoe_cbmp
import config

DB_PATH = config.DB_PATH
LOG_PATH = os.path.join(config.LOG_DIR, "mffr_price_fetch_errors.log")
tz = pytz.timezone("Europe/Tallinn")
scheduler = BackgroundScheduler()

# Baltic Transparency Dashboard (Elering / AST / Litgrid) — mFRR balancing energy prices, €/MWh, 15 min
BTD_URL = "https://api-baltic.transparency-dashboard.eu/api/v1/export"
PRICE_AREA = os.getenv("MFRR_PRICE_AREA", "Estonia")
MAX_LOOKBACK = timedelta(days=7)
SLOT = timedelta(minutes=15)
# The dashboard is only queried when a finished slot is missing its price, and then at most
# this often (prices are published with a delay of about 1–3 h)
DEFAULT_RECHECK_MIN = 5
RECHECK = timedelta(minutes=float(os.getenv("MFRR_PRICE_RECHECK_MIN", DEFAULT_RECHECK_MIN)))

# Last sync result, served by /api/price-sync
sync_status = {
    "source": "Baltic Transparency Dashboard",
    "area": PRICE_AREA,
    "last_sync_at": None,         # last attempt
    "last_success_at": None,
    "last_error": None,
    "latest_price_slot": None,    # start of the newest slot with a published price
    "latest_up_price": None,      # €/MWh
    "latest_down_price": None,    # €/MWh
    "pending_slots": 0,           # finished slots still waiting for a price
    "next_sync_at": None,         # None = no sync needed until a finished slot lacks a price
    # aFRR: estimated until Volton publishes the aFRR clearing price for the slot
    "afrr_source": "Volton aFRR clearing price",
    "afrr_last_check_at": None,
    "afrr_last_success_at": None,
    "afrr_last_error": None,
    "afrr_estimated_slots": 0,
    "afrr_next_check_at": None,   # None = no estimated slot waits for Volton
    # aFRR market price (CBMP) shown for comparison; income keeps the estimate
    "cbmp_source": entsoe_cbmp.SOURCE,
    "cbmp_configured": entsoe_cbmp.configured(),
    "cbmp_last_check_at": None,
    "cbmp_last_success_at": None,
    "cbmp_last_error": None,
    "cbmp_pending_slots": 0,
    "cbmp_next_check_at": None,   # None = no aFRR slot waits for its market price
}
CBMP_MAX_AGE = timedelta(days=10)
CBMP_INTERVAL = timedelta(minutes=30)   # today's prices are re-fetched at most hourly (TODAY_REFRESH)

VOLTON_AFRR_URL = "https://public-data.volton.energy/v1/afrr-clearing-price/latest.json"
AFRR_RECHECK = timedelta(hours=1)
AFRR_MAX_AGE = timedelta(days=3)    # stop looking for a published price after this

# Ensure log folder exists
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
entsoe_cbmp.migrate()

def log_error(message):
    with open(LOG_PATH, "a") as f:
        timestamp = datetime.now(tz).isoformat()
        f.write(f"[{timestamp}] {message}\n")

def fetch_btd_prices(start: datetime, end: datetime) -> dict:
    """Return {utc_slot_start: {"UP": price, "DOWN": price}} for PRICE_AREA."""
    response = requests.get(
        BTD_URL,
        params={
            "id": "balancing_energy_prices",
            "start_date": start.astimezone(tz).strftime("%Y-%m-%dT%H:%M"),
            "end_date": end.astimezone(tz).strftime("%Y-%m-%dT%H:%M"),
            "output_time_zone": "EET",
            "output_format": "json",
            "second_resolution": 900,
        },
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()["data"]

    columns = {}
    for col in data["columns"]:
        if col.get("group_level_0") == PRICE_AREA:
            label = col.get("label", "").lower()
            if label == "upward":
                columns["UP"] = col["index"]
            elif label == "downward":
                columns["DOWN"] = col["index"]
    if len(columns) != 2:
        raise ValueError(f"Up/Down price columns for {PRICE_AREA} not found in {data['columns']}")

    prices = {}
    for entry in data["timeseries"]:
        slot_start = datetime.fromisoformat(entry["from"])
        values = entry["values"]
        prices[slot_start] = {direction: values[i] for direction, i in columns.items()}
    return prices

def apply_mffr_prices(db, rows, api_data: dict) -> int:
    """Set each row's mFRR price for its direction (UP → upward, DOWN → downward); returns count."""
    updated = 0
    for row in rows:
        try:
            # an mFRR ramp minute is priced with the next quarter (price_timeslot)
            slot_start = datetime.fromisoformat(row.get("price_timeslot") or row["timeslot"]).astimezone(pytz.utc)
            mfrr_price = (api_data.get(slot_start) or {}).get(row["signal"])

            if mfrr_price is not None:
                db["slots"].update(
                    row["id"],
                    {"mffr_price": mfrr_price},
                    alter=True
                )
                updated += 1
                print(f"📡 Set mFRR {row['signal']} price {mfrr_price} for slot {row['timeslot']}")
        except Exception as e:
            msg = f"⚠️ Failed to update mFRR price for slot {row['timeslot']}: {e}"
            print(msg)
            log_error(msg)
    return updated

def fetch_and_update_mffr_prices():
    start_time = time.time()
    db = sqlite_utils.Database(DB_PATH)

    now = datetime.now(tz)
    pending = []
    if "slots" in db.table_names():
        rows = db["slots"].rows_where(
            "mffr_price IS NULL AND COALESCE(market, 'MFRR') = 'MFRR' AND timeslot >= ?",
            [(now - MAX_LOOKBACK).isoformat()],
            order_by="timeslot",
        )
        # A running slot can't have a published price yet
        pending = [r for r in rows if datetime.fromisoformat(r["timeslot"]) + SLOT <= now]
    sync_status["pending_slots"] = len(pending)

    # Only query the dashboard when a finished slot needs a price, and not more often than RECHECK
    if not pending:
        sync_status["next_sync_at"] = None
        return
    last = sync_status["last_sync_at"]
    if last and now - datetime.fromisoformat(last) < RECHECK:
        sync_status["next_sync_at"] = (datetime.fromisoformat(last) + RECHECK).isoformat()
        return

    window_start = datetime.fromisoformat(pending[0]["timeslot"])
    sync_status["last_sync_at"] = now.isoformat()
    sync_status["next_sync_at"] = (now + RECHECK).isoformat()
    try:
        api_data = fetch_btd_prices(window_start, now)
    except Exception as e:
        msg = f"❌ Failed to fetch mFRR prices: {e}"
        sync_status["last_error"] = str(e)
        print(msg)
        log_error(msg)
        return

    sync_status["last_success_at"] = now.isoformat()
    sync_status["last_error"] = None
    published = [slot for slot, p in api_data.items() if p["UP"] is not None or p["DOWN"] is not None]
    if published:
        latest = max(published)
        sync_status["latest_price_slot"] = latest.astimezone(tz).isoformat()
        sync_status["latest_up_price"] = api_data[latest]["UP"]
        sync_status["latest_down_price"] = api_data[latest]["DOWN"]

    updated = apply_mffr_prices(db, pending, api_data)

    sync_status["pending_slots"] = len(pending) - updated
    if not sync_status["pending_slots"]:
        sync_status["next_sync_at"] = None
    if updated:
        print(f"✅ Updated {updated} mFRR prices in SQLite DB.")
        print(f"⏱️ Completed in {time.time() - start_time:.2f} seconds.")

def fetch_volton_afrr_prices() -> dict:
    """{(utc_slot_start, "UP"/"DOWN"): €/MWh} for slots Volton has published."""
    resp = requests.get(VOLTON_AFRR_URL, timeout=15)
    resp.raise_for_status()
    prices = {}
    for r in resp.json().get("rows", []):
        price = r.get("price_eur_mwh")
        if price is not None:
            start = datetime.fromisoformat(r["mtu_start"].replace("Z", "+00:00"))
            prices[(start, r["direction"].upper())] = price
    return prices

def update_afrr_prices():
    """Replace estimated aFRR prices with Volton's clearing price once published (on demand)."""
    db = sqlite_utils.Database(DB_PATH)
    if "slots" not in db.table_names() or "price_source" not in db["slots"].columns_dict:
        return
    now = datetime.now(tz)
    rows = [r for r in db["slots"].rows_where(
                "market = 'AFRR' AND price_source = 'estimate' AND timeslot >= ?",
                [(now - AFRR_MAX_AGE).isoformat()])
            if datetime.fromisoformat(r["timeslot"]) + SLOT <= now]
    sync_status["afrr_estimated_slots"] = len(rows)
    last = sync_status["afrr_last_check_at"]
    if not rows:
        sync_status["afrr_next_check_at"] = None
        return
    if last and now - datetime.fromisoformat(last) < AFRR_RECHECK:
        sync_status["afrr_next_check_at"] = (datetime.fromisoformat(last) + AFRR_RECHECK).isoformat()
        return
    sync_status["afrr_last_check_at"] = now.isoformat()
    sync_status["afrr_next_check_at"] = (now + AFRR_RECHECK).isoformat()
    try:
        prices = fetch_volton_afrr_prices()
        sync_status["afrr_last_error"] = None
        sync_status["afrr_last_success_at"] = now.isoformat()
    except Exception as e:
        sync_status["afrr_last_error"] = str(e)
        log_error(f"❌ Failed to fetch Volton aFRR prices: {e}")
        return
    updated = 0
    for r in rows:
        start = datetime.fromisoformat(r.get("price_timeslot") or r["timeslot"]).astimezone(pytz.utc)
        price = prices.get((start, r["signal"]))
        if price is not None:
            # clear the profit so profit_calc recomputes it with the real price
            db["slots"].update(r["id"], {"mffr_price": price, "price_source": "volton",
                                         "profit": None, "net_total": None})
            updated += 1
    sync_status["afrr_estimated_slots"] = len(rows) - updated
    if updated:
        print(f"✅ Set Volton aFRR price for {updated} slot(s)")

def update_afrr_cbmp():
    """Fill the aFRR market price (ENTSO-E PICASSO CBMP) on finished aFRR rows, on demand.
    Never touches mffr_price / profit: the income stays on the estimate, Volton or the report."""
    db = sqlite_utils.Database(DB_PATH)
    if "slots" not in db.table_names() or "cbmp_points" not in db["slots"].columns_dict:
        return
    now = datetime.now(tz)
    rows = [r for r in db["slots"].rows_where(
                "market = 'AFRR' AND timeslot >= ? AND (cbmp_points IS NULL OR (cbmp_points = 0 AND timeslot >= ?))",
                [(now - CBMP_MAX_AGE).isoformat(), (now - timedelta(days=1)).isoformat()])
            if datetime.fromisoformat(r["timeslot"]) + SLOT <= now]
    sync_status["cbmp_pending_slots"] = len(rows)   # also without a token: the UI says what waits
    if not rows or not entsoe_cbmp.configured():
        sync_status["cbmp_next_check_at"] = None
        return
    sync_status["cbmp_last_check_at"] = now.isoformat()
    sync_status["cbmp_next_check_at"] = (now + CBMP_INTERVAL).isoformat()
    try:
        updated = entsoe_cbmp.fill_rows(db, rows)
        sync_status["cbmp_last_error"] = None
        sync_status["cbmp_last_success_at"] = now.isoformat()
        if updated:
            print(f"✅ Set aFRR market price (CBMP) on {updated} row(s)")
    except Exception as e:
        sync_status["cbmp_last_error"] = str(e)
        log_error(f"❌ ENTSO-E CBMP: {e}")

scheduler.add_job(
    update_afrr_cbmp,
    "interval",
    id="afrr_cbmp",
    seconds=CBMP_INTERVAL.total_seconds(),
    next_run_time=datetime.now(tz),   # also right after startup,
    misfire_grace_time=None,          # however long the startup takes
    max_instances=1,
    coalesce=True
)

scheduler.add_job(
    update_afrr_prices,
    "interval",
    id="afrr_prices",
    minutes=5,
    next_run_time=datetime.now(tz),   # also right after startup, so the status is current
    misfire_grace_time=None,
    max_instances=1,
    coalesce=True
)

scheduler.add_job(
    fetch_and_update_mffr_prices,
    "interval",
    id="mffr_prices",
    minutes=1,
    max_instances=1,
    coalesce=True
)
