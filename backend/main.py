# main.py
import os
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
from sqlite_utils import Database
from sqlite_utils.db import NotFoundError

from ha import SENSOR_NORDPOOL, GridMeter, get_entity, get_requested_w, get_signal, mffr_energy_kwh
from baseline import SignalBaseline

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
    "id": str,             # "<timeslot>_<signal>": one row per direction, a slot can flip
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
}, pk="id", if_not_exists=True)

# Migrate one-row-per-slot tables (pk timeslot) to one row per slot and direction
if "id" not in init_db["slots"].columns_dict:
    print("🛠️  Migrating 'slots' to one row per slot and direction")
    init_db["slots"].add_column("id", str)
    with init_db.conn:
        init_db.conn.execute("UPDATE slots SET id = timeslot || '_' || COALESCE(signal, '')")
    init_db["slots"].transform(pk="id")

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
    "metered_s": float,        # seconds covered by meter reads
    "baseline_import_kwh": float,  # what the baseline would have imported over the metered time
    "baseline_export_kwh": float,  # what the baseline would have exported over the metered time
    "requested_kwh": float,    # energy Kratt asked for (qw_powerlimit × metered time)
    "delivery_pct": float,     # delivered mFRR energy / requested energy
    "active_s": float,         # seconds this direction was active in the slot
    "baseline_w": float        # locked baseline of the (latest) run in this slot
}
for column, col_type in required_columns.items():
    if column not in init_db["slots"].columns_dict:
        print(f"🛠️  Adding missing column '{column}' to 'slots' table")
        init_db["slots"].add_column(column, col_type)

init_db["slots"].create_index(["timeslot"], if_not_exists=True)
init_db["slots"].create_index(["duration_min", "end"], if_not_exists=True)

last_logged_signal = None
grid_meter = GridMeter()
signal_baseline = SignalBaseline()

def _with_busy_timeout(db: Database, ms: int = 5000):
    try:
        db.conn.execute(f"PRAGMA busy_timeout={ms};")
    except Exception:
        pass

def cleanup_zero_min_rows():
    db = Database(DB_PATH)
    _with_busy_timeout(db)
    try:
        cutoff = (datetime.now(tz) - timedelta(minutes=2)).isoformat()
        with db.conn:
            # Spurious rows (e.g. a stale signal at a slot boundary); short legs of a direction
            # flip that actually delivered energy are kept
            db.conn.execute(
                "DELETE FROM slots WHERE duration_min = 0 AND end < ? AND COALESCE(energy_kwh, 0) < 0.01",
                (cutoff,)
            )
    except Exception as e:
        if "locked" in str(e).lower():
            print("🧹 Cleanup skipped (database locked).")
        else:
            print(f"🧹 Scheduled cleanup failed: {e}")

def slot_id(timeslot: str, signal: str) -> str:
    return f"{timeslot}_{signal}"

def write_current_timeslot():
    global last_logged_signal
    db = Database(DB_PATH)
    _with_busy_timeout(db)

    now = datetime.now(tz).replace(microsecond=0)
    minute = (now.minute // 15) * 15
    timeslot = now.replace(minute=minute, second=0)
    key = timeslot.isoformat()
    slot_end_time = timeslot + timedelta(minutes=15)

    # Read the meter every tick so the next interval only covers the last ~10 s
    reading = grid_meter.read(now, slot_start=timeslot)
    signal = get_signal()
    # Snapshot of the grid state just before the signal, locked for the run
    baseline_w = signal_baseline.on_tick(now, reading, active=signal is not None)

    if signal != last_logged_signal:
        print(f"🔔 Signal became {signal} at {now.isoformat()}")
        last_logged_signal = signal

    if not signal:
        return

    row_id = slot_id(key, signal)
    try:
        row = db["slots"].get(row_id)
    except NotFoundError:
        row = None

    # Grid energy since the previous tick, integrated from the phase power sensors
    net_kwh, seconds = reading if reading else (0.0, 0.0)
    requested_w = get_requested_w()

    def totals(prev: dict | None) -> dict:
        """Add this tick to the slot totals; mFRR energy is evaluated over the whole slot."""
        prev = prev or {}
        grid_kwh = (prev.get("grid_kwh") or 0.0) + net_kwh
        metered_s = (prev.get("metered_s") or 0.0) + seconds
        # Baseline energy is accumulated per tick, so several runs in one slot keep their own baseline
        baseline_tick_kwh = baseline_w * seconds / 3_600_000.0
        baseline_import = (prev.get("baseline_import_kwh") or 0.0) + max(0.0, baseline_tick_kwh)
        baseline_export = (prev.get("baseline_export_kwh") or 0.0) + max(0.0, -baseline_tick_kwh)
        energy_kwh = mffr_energy_kwh(signal, grid_kwh, baseline_import - baseline_export)
        requested_kwh = (prev.get("requested_kwh") or 0.0) + (requested_w or 0.0) / 1000.0 * seconds / 3600.0
        return {
            "grid_kwh": round(grid_kwh, 5),
            "grid_import_kwh": round((prev.get("grid_import_kwh") or 0.0) + max(0.0, net_kwh), 5),
            "grid_export_kwh": round((prev.get("grid_export_kwh") or 0.0) + max(0.0, -net_kwh), 5),
            "metered_s": round(metered_s, 1),
            "baseline_import_kwh": round(baseline_import, 5),
            "baseline_export_kwh": round(baseline_export, 5),
            "baseline_w": baseline_w,
            "energy_kwh": round(energy_kwh, 5),
            "requested_kwh": round(requested_kwh, 5),
            "delivery_pct": round(energy_kwh / requested_kwh * 100.0, 1) if requested_kwh > 0 else None,
        }

    if row:
        end_time = datetime.fromisoformat(row["end"])
        if end_time < slot_end_time:
            start_time = datetime.fromisoformat(row["start"])
            # Count only time this direction was active: after a flip and back, the gap since
            # this row's last tick is not added (capped at about one tick)
            since_last_s = (now - end_time).total_seconds()
            active_s = (row.get("active_s") or 0.0) + (since_last_s if since_last_s <= 20 else 10.0)
            cancelled = now < (slot_end_time - timedelta(seconds=11))
            was_backup = (start_time - timeslot).total_seconds() >= 15

            update_data = {
                **totals(row),
                "end": now.isoformat(),
                "active_s": round(active_s, 1),
                "duration_min": round(active_s / 60),
                "cancelled": cancelled,
                "was_backup": was_backup,
                "slot_end": slot_end_time.isoformat(),
            }
            db["slots"].update(row_id, update_data)
    else:
        if (now - timeslot).total_seconds() < 5:
            return

        try:
            prev_slot_time = timeslot - timedelta(minutes=15)
            previous = db["slots"].get(slot_id(prev_slot_time.isoformat(), signal))
            previous_end = datetime.fromisoformat(previous["end"])
            if abs((now - previous_end).total_seconds()) <= 7:
                return
        except NotFoundError:
            pass

        entry = {
            "id": row_id,
            "timeslot": key,
            "start": now.isoformat(),
            "end": now.isoformat(),
            "signal": signal,
            **totals(None),
            "mffr_price": None,
            "nordpool_price": None,
            "profit": None,
            "duration_min": 0,
            "active_s": seconds or 10.0,
            "cancelled": False,
            "was_backup": False,
            "slot_end": slot_end_time.isoformat(),
        }
        db["slots"].insert(entry, pk="id", alter=True)

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
                    row = db["slots"].get(row_id)
                    if row.get("nordpool_price") is None:
                        db["slots"].update(row_id, {"nordpool_price": price})
                        print(f"📈 Set Nordpool price {price} €/kWh for slot {row_id}")
                except NotFoundError:
                    pass
                break
    except Exception as e:
        print(f"❌ Failed to fetch Nordpool price: {e}")

# Scheduler is started by FastAPI (api.py)
scheduler = BackgroundScheduler()
scheduler.add_job(write_current_timeslot, 'interval', seconds=10, max_instances=1, coalesce=True)
scheduler.add_job(cleanup_zero_min_rows, 'interval', minutes=1, max_instances=1, coalesce=True)