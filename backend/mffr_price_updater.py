import requests
import sqlite_utils
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
import time
import os

DB_PATH = "data/mffr.db"
LOG_PATH = "logs/mffr_price_fetch_errors.log"
tz = pytz.timezone("Europe/Tallinn")
scheduler = BackgroundScheduler()

# Baltic Transparency Dashboard (Elering / AST / Litgrid) — mFRR balancing energy prices, €/MWh, 15 min
BTD_URL = "https://api-baltic.transparency-dashboard.eu/api/v1/export"
PRICE_AREA = os.getenv("MFRR_PRICE_AREA", "Estonia")
MAX_LOOKBACK = timedelta(days=7)
SLOT = timedelta(minutes=15)
# The dashboard is only queried when a finished slot is missing its price, and then at most
# this often (prices are published with a delay of about 1–3 h)
RECHECK = timedelta(minutes=float(os.getenv("MFRR_PRICE_RECHECK_MIN", "5")))

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
}

# Ensure log folder exists
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

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
            slot_start = datetime.fromisoformat(row["timeslot"]).astimezone(pytz.utc)
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
            "mffr_price IS NULL AND timeslot >= ?",
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

scheduler.add_job(
    fetch_and_update_mffr_prices,
    "interval",
    id="mffr_prices",
    minutes=1,
    max_instances=1,
    coalesce=True
)
