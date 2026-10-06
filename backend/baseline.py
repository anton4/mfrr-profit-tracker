# backend/baseline.py
# Tracks average net grid power (Kratt meters at the grid connection point) during idle slots,
# from the cumulative import/export energy counters.
from datetime import datetime
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from sqlite_utils import Database

from ha import GridMeter, get_signal

DB_PATH = "data/mffr.db"
tz = pytz.timezone("Europe/Tallinn")

def dlog(msg: str):
    print(f"[baseline] {datetime.now(tz).isoformat()}  {msg}")

def _open_db() -> Database:
    db = Database(DB_PATH)
    try:
        db.conn.execute("PRAGMA journal_mode=WAL;")
        db.conn.execute("PRAGMA synchronous=NORMAL;")
        db.conn.execute("PRAGMA busy_timeout=5000;")
    except Exception:
        pass
    return db

def _ensure_schema():
    db = _open_db()
    try:
        db["baseline_state"].create({
            "key": str,
            "baseline_w": float,
            "computed_for_slot": str,
            "energy_Wh": float,
            "updated_at": str
        }, pk="key", if_not_exists=True)
    finally:
        try:
            db.conn.close()
        except Exception:
            pass

_ensure_schema()

def reset_baseline_table():
    try:
        db = Database(DB_PATH)
        db["baseline_state"].delete_where("1=1")
        db.conn.commit()
        print("🧹 Cleared baseline_state on startup")
    except Exception as e:
        print(f"❌ Failed to clear baseline_state: {e}")

reset_baseline_table()

# A slot only yields a baseline if the meter covered most of it (e.g. not right after startup)
MIN_COVERAGE_S = 720

meter = GridMeter()
accum_Wh = 0.0
accum_s = 0.0
saw_mffr = False
current_slot = None

def _slot_anchor(dt: datetime):
    return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)

def tick():
    global accum_Wh, accum_s, saw_mffr, current_slot
    now = datetime.now(tz)
    slot = _slot_anchor(now)

    if current_slot is None:
        current_slot = slot

    if slot > current_slot:
        if accum_s >= MIN_COVERAGE_S and not saw_mffr:
            avg_w = round((accum_Wh * 3600.0) / accum_s, 2)
            try:
                db = _open_db()
                with db.conn:
                    db["baseline_state"].upsert({
                        "key": "latest",
                        "baseline_w": avg_w,
                        "computed_for_slot": current_slot.isoformat(),
                        "energy_Wh": round(accum_Wh, 3),
                        "updated_at": now.isoformat()
                    }, pk="key")
                dlog(f"Updated baseline: {avg_w} W (slot {current_slot.isoformat()}, energy {accum_Wh:.3f} Wh)")
            except Exception:
                pass
            finally:
                try:
                    db.conn.close()
                except Exception:
                    pass

        current_slot = slot
        accum_Wh = 0.0
        accum_s = 0.0
        saw_mffr = False

    reading = meter.read(now)

    sig = get_signal()
    if sig and not saw_mffr:
        saw_mffr = True

    if reading:
        net_kwh, seconds = reading
        accum_Wh += net_kwh * 1000.0
        accum_s += seconds

scheduler = BackgroundScheduler()
scheduler.add_job(tick, "interval", seconds=10, max_instances=1, coalesce=True)

if __name__ == "__main__":
    print("▶️ baseline service started")
    scheduler.start()
    import time
    while True:
        time.sleep(3600)