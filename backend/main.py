# main.py
import os
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
from sqlite_utils import Database
from sqlite_utils.db import NotFoundError

from ha import SENSOR_NORDPOOL, GridMeter, get_entity, get_signal, mffr_energy_kwh

DB_PATH = "data/mffr.db"
tz = pytz.timezone("Europe/Tallinn")

# --- DB schema bootstrap ---
init_db = Database(DB_PATH)
try:
    init_db.conn.execute("PRAGMA journal_mode=WAL;")
    init_db.conn.execute("PRAGMA synchronous=NORMAL;")
    init_db.conn.execute("PRAGMA busy_timeout=5000;")
except Exception as e:
    print(f"⚙️ PRAGMA setup failed: {e}")

init_db["slots"].create({
    "timeslot": str,
    "start": str,
    "end": str,
    "signal": str,
    "energy_kwh": float,
    "grid_kwh": float,
    "mffr_price": float,
    "nordpool_price": float,
    "profit": float,
    "duration_min": int,
    "cancelled": bool,
    "was_backup": bool,
    "slot_end": str
}, pk="timeslot", if_not_exists=True)

# Migrate legacy Fusebox column name
if "fusebox_fee" in init_db["slots"].columns_dict and "kratt_fee" not in init_db["slots"].columns_dict:
    print("🛠️  Renaming column 'fusebox_fee' → 'kratt_fee'")
    init_db.conn.execute("ALTER TABLE slots RENAME COLUMN fusebox_fee TO kratt_fee")

required_columns = {
    "grid_cost": float,
    "ffr_income": float,
    "kratt_fee": float,
    "net_total": float,
    "price_per_kwh": float,
    "grid_kwh": float,         # net grid energy (+import / -export)
    "grid_import_kwh": float,  # import part, netted across phases per read
    "grid_export_kwh": float,  # export part, netted across phases per read
    "metered_s": float,        # seconds covered by meter reads (for baseline energy)
    "baseline_w": float        # snapshot of baseline per slot
}
for column, col_type in required_columns.items():
    if column not in init_db["slots"].columns_dict:
        print(f"🛠️  Adding missing column '{column}' to 'slots' table")
        init_db["slots"].add_column(column, col_type)

init_db["slots"].create_index(["timeslot"], if_not_exists=True)
init_db["slots"].create_index(["duration_min", "end"], if_not_exists=True)

last_logged_signal = None
grid_meter = GridMeter()

def _with_busy_timeout(db: Database, ms: int = 5000):
    try:
        db.conn.execute(f"PRAGMA busy_timeout={ms};")
    except Exception:
        pass

def get_latest_baseline_w() -> float:
    try:
        db = Database(DB_PATH)
        row = db["baseline_state"].get("latest")
        return float(row["baseline_w"])
    except Exception:
        return 0.0  # Default fallback if not available

def cleanup_zero_min_rows():
    db = Database(DB_PATH)
    _with_busy_timeout(db)
    try:
        cutoff = (datetime.now(tz) - timedelta(minutes=2)).isoformat()
        with db.conn:
            db.conn.execute(
                "DELETE FROM slots WHERE duration_min = 0 AND end < ?",
                (cutoff,)
            )
    except Exception as e:
        if "locked" in str(e).lower():
            print("🧹 Cleanup skipped (database locked).")
        else:
            print(f"🧹 Scheduled cleanup failed: {e}")

def write_current_timeslot():
    global last_logged_signal
    db = Database(DB_PATH)
    _with_busy_timeout(db)

    now = datetime.now(tz).replace(microsecond=0)
    minute = (now.minute // 15) * 15
    timeslot = now.replace(minute=minute, second=0)
    key = timeslot.isoformat()
    slot_end_time = timeslot + timedelta(minutes=15)

    # Read the counters every tick so the next delta only covers the last ~10 s
    reading = grid_meter.read(now)
    signal = get_signal()

    if signal != last_logged_signal:
        print(f"🔔 Signal became {signal} at {now.isoformat()}")
        last_logged_signal = signal

    if not signal:
        return

    try:
        row = db["slots"].get(key)
    except NotFoundError:
        row = None

    # Baseline is locked per command run: reuse the snapshot stored on this slot,
    # else carry it over from the directly preceding slot of the same run,
    # else take the latest idle-slot baseline.
    baseline_w = row.get("baseline_w") if row and row["signal"] == signal else None
    if baseline_w is None:
        try:
            previous = db["slots"].get((timeslot - timedelta(minutes=15)).isoformat())
            if previous.get("baseline_w") is not None and \
                    (timeslot - datetime.fromisoformat(previous["end"])).total_seconds() <= 30:
                baseline_w = previous["baseline_w"]
        except NotFoundError:
            pass
    if baseline_w is None:
        baseline_w = get_latest_baseline_w()

    # Grid energy since the previous tick, from the cumulative meter counters
    net_kwh, seconds = reading if reading else (0.0, 0.0)

    def totals(prev: dict | None) -> dict:
        """Add this tick to the slot totals; mFRR energy is evaluated over the whole slot."""
        prev = prev or {}
        grid_kwh = (prev.get("grid_kwh") or 0.0) + net_kwh
        metered_s = (prev.get("metered_s") or 0.0) + seconds
        return {
            "grid_kwh": round(grid_kwh, 5),
            "grid_import_kwh": round((prev.get("grid_import_kwh") or 0.0) + max(0.0, net_kwh), 5),
            "grid_export_kwh": round((prev.get("grid_export_kwh") or 0.0) + max(0.0, -net_kwh), 5),
            "metered_s": round(metered_s, 1),
            "energy_kwh": round(mffr_energy_kwh(signal, grid_kwh, metered_s, baseline_w), 5),
        }

    if row and row["signal"] == signal:
        end_time = datetime.fromisoformat(row["end"])
        if end_time < slot_end_time:
            start_time = datetime.fromisoformat(row["start"])
            duration = round((now - start_time).total_seconds() / 60)
            cancelled = now < (slot_end_time - timedelta(seconds=11))
            was_backup = (start_time - timeslot).total_seconds() >= 15

            update_data = {
                "timeslot": key,
                **totals(row),
                "end": now.isoformat(),
                "duration_min": duration,
                "cancelled": cancelled,
                "was_backup": was_backup,
                "slot_end": slot_end_time.isoformat(),
            }
            if row.get("baseline_w") is None:
                update_data["baseline_w"] = baseline_w

            db["slots"].update(key, update_data)
    else:
        if (now - timeslot).total_seconds() < 5:
            return

        try:
            prev_slot_time = timeslot - timedelta(minutes=15)
            previous = db["slots"].get(prev_slot_time.isoformat())
            previous_end = datetime.fromisoformat(previous["end"])
            if previous["signal"] == signal and abs((now - previous_end).total_seconds()) <= 7:
                return
        except NotFoundError:
            pass

        entry = {
            "timeslot": key,
            "start": now.isoformat(),
            "end": now.isoformat(),
            "signal": signal,
            **totals(None),
            "mffr_price": None,
            "nordpool_price": None,
            "profit": None,
            "duration_min": 0,
            "cancelled": False,
            "was_backup": False,
            "slot_end": slot_end_time.isoformat(),
            "baseline_w": baseline_w,
        }
        db["slots"].insert(entry, pk="timeslot", replace=True)

    try:
        attrs = (get_entity(SENSOR_NORDPOOL) or {}).get("attributes", {})
        raw_today = attrs.get("raw_today", []) or []
        raw_tomorrow = attrs.get("raw_tomorrow", []) or []
        for p in (raw_today + raw_tomorrow):
            start = datetime.fromisoformat(p["start"])
            end = datetime.fromisoformat(p["end"])
            if start <= timeslot < end:
                price = round(p["value"], 5)
                try:
                    row = db["slots"].get(key)
                    if row.get("nordpool_price") is None:
                        db["slots"].update(key, {"nordpool_price": price})
                        print(f"📈 Set Nordpool price {price} €/kWh for slot {key}")
                except NotFoundError:
                    pass
                break
    except Exception as e:
        print(f"❌ Failed to fetch Nordpool price: {e}")

# Scheduler is started by FastAPI (api.py)
scheduler = BackgroundScheduler()
scheduler.add_job(write_current_timeslot, 'interval', seconds=10, max_instances=1, coalesce=True)
scheduler.add_job(cleanup_zero_min_rows, 'interval', minutes=1, max_instances=1, coalesce=True)