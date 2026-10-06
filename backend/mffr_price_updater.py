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
PRICE_AREA = os.getenv("MFFR_PRICE_AREA", "Estonia")
MAX_LOOKBACK = timedelta(days=7)

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

def fetch_and_update_mffr_prices():
    start_time = time.time()
    db = sqlite_utils.Database(DB_PATH)
    updated = 0

    if "slots" not in db.table_names():
        return

    now = datetime.now(tz)
    pending = list(db["slots"].rows_where(
        "mffr_price IS NULL AND timeslot >= ?",
        [(now - MAX_LOOKBACK).isoformat()],
        order_by="timeslot",
    ))
    if not pending:
        return

    try:
        window_start = datetime.fromisoformat(pending[0]["timeslot"])
        api_data = fetch_btd_prices(window_start, now)
    except Exception as e:
        msg = f"❌ Failed to fetch MFFR prices: {e}"
        print(msg)
        log_error(msg)
        return

    for row in pending:
        try:
            slot_start = datetime.fromisoformat(row["timeslot"]).astimezone(pytz.utc)
            mfrr_price = (api_data.get(slot_start) or {}).get(row["signal"])

            if mfrr_price is not None:
                db["slots"].update(
                    row["timeslot"],
                    {"mffr_price": mfrr_price},
                    alter=True
                )
                updated += 1
                print(f"📡 Set MFFR {row['signal']} price {mfrr_price} for slot {row['timeslot']}")
        except Exception as e:
            msg = f"⚠️ Failed to update MFFR price for slot {row['timeslot']}: {e}"
            print(msg)
            log_error(msg)

    if updated:
        print(f"✅ Updated {updated} MFFR prices in SQLite DB.")
    print(f"⏱️ Completed in {time.time() - start_time:.2f} seconds.")

scheduler.add_job(
    fetch_and_update_mffr_prices,
    "interval",
    minutes=1,
    max_instances=1,
    coalesce=True
)
