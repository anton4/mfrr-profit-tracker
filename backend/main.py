# main.py
import os
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
from sqlite_utils import Database
from sqlite_utils.db import NotFoundError

from ha import (SENSOR_NORDPOOL, GridMeter, classify_market, deviation_direction, get_entity,
                get_requested_w, get_signal, get_source_changed)
from baseline import SignalBaseline

DB_PATH = "data/mffr.db"
tz = pytz.timezone("Europe/Tallinn")
SLOT = timedelta(minutes=15)

# aFRR energy prices aren't published reliably yet: estimate (€/MWh) until Volton or a
# Qilowatt report gives the real figure. Defaults are the rates implied by Kratt reports
# (UP revenue ≈ 440 €/MWh, DOWN ≈ 530 €/MWh paid for absorbing → price −530).
AFRR_PRICE_UP = float(os.getenv("AFRR_PRICE_UP_EUR_MWH", "440"))
AFRR_PRICE_DOWN = float(os.getenv("AFRR_PRICE_DOWN_EUR_MWH", "-530"))

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
    "baseline_w": float,       # locked baseline of the (latest) run in this slot
    "market": str,             # MFRR / AFRR (NULL for rows recorded before market detection)
    "price_timeslot": str,     # slot whose prices apply (next quarter for an mFRR ramp minute)
    "price_source": str,       # btd / volton / estimate
}
for column, col_type in required_columns.items():
    if column not in init_db["slots"].columns_dict:
        print(f"🛠️  Adding missing column '{column}' to 'slots' table")
        init_db["slots"].add_column(column, col_type)

init_db["slots"].create_index(["timeslot"], if_not_exists=True)
init_db["slots"].create_index(["duration_min", "end"], if_not_exists=True)

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

def slot_id(timeslot: str, market: str, signal: str, ramp: bool = False) -> str:
    return f"{timeslot}_{market}_{signal}" + ("_r" if ramp else "")

class Tracker:
    """Turns ticks of HA state into slot rows. The live job has one instance; a backfill
    replays recorded history through its own instance with the same logic.

    Rows are per slot, market (MFRR/AFRR) and direction. Like Kratt, energy is split by the
    sign of (grid − baseline) on every tick of a run — including an aFRR restore phase where
    qw_mode may show the opposite direction — while requested energy and activity follow the
    commanded direction.
    """

    def __init__(self, store_baseline: bool = True):
        self.meter = GridMeter()
        self.baseline = SignalBaseline(store=store_baseline)
        self.last_logged_signal = None
        self.run_market = None   # market of the current Kratt run

    def tick(self, now: datetime, fetch=None, write=True, live=True, db: Database | None = None):
        """Process one 10 s tick at `now`.

        fetch:  HA state reader (None = live HA); a backfill passes recorded history.
        write:  False for warm-up ticks that only feed the meter and baseline.
        live:   False skips the Nord Pool sensor (a backfill sets prices itself).
        """
        if db is None:
            db = Database(DB_PATH)
            _with_busy_timeout(db)

        timeslot = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
        key = timeslot.isoformat()
        slot_end_time = timeslot + SLOT

        # Read the meter every tick so the next interval only covers the last ~10 s
        reading = self.meter.read(now, slot_start=timeslot, fetch=fetch)
        signal = get_signal(fetch)
        # Snapshot of the grid state just before the signal, locked for the run
        baseline_w = self.baseline.on_tick(now, reading, active=signal is not None)

        if signal != self.last_logged_signal:
            if live:
                print(f"🔔 Signal became {signal} at {now.isoformat()}")
            self.last_logged_signal = signal

        if not signal:
            self.run_market = None
            return
        if self.run_market is None:
            run_start = get_source_changed(fetch) or now
            self.run_market = classify_market(run_start.astimezone(tz))
            if live:
                print(f"🏷️  Kratt run from {run_start.astimezone(tz).isoformat()} classified as {self.run_market}")
        if not write:
            return
        market = self.run_market

        # An mFRR activation starts one minute before its quarter; Kratt books that minute in
        # the current slot but prices it with the next quarter's prices
        ramp = market == "MFRR" and now.minute % 15 == 14
        price_slot = (slot_end_time if ramp else timeslot).isoformat()

        net_kwh, seconds = reading if reading else (0.0, 0.0)
        baseline_kwh = baseline_w * seconds / 3_600_000.0
        deviation_kwh = net_kwh - baseline_kwh
        requested_w = get_requested_w(fetch)

        def new_row(direction: str) -> dict:
            row = {
                "id": slot_id(key, market, direction, ramp),
                "timeslot": key, "price_timeslot": price_slot, "market": market, "signal": direction,
                "start": now.isoformat(), "end": now.isoformat(), "slot_end": slot_end_time.isoformat(),
                "energy_kwh": 0.0, "grid_kwh": 0.0, "grid_import_kwh": 0.0, "grid_export_kwh": 0.0,
                "metered_s": 0.0, "baseline_import_kwh": 0.0, "baseline_export_kwh": 0.0,
                "baseline_w": baseline_w, "requested_kwh": 0.0, "delivery_pct": None,
                "active_s": 0.0, "duration_min": 0, "cancelled": False, "was_backup": False,
                "mffr_price": None, "nordpool_price": None, "profit": None, "price_source": None,
            }
            if market == "AFRR":
                row["mffr_price"] = AFRR_PRICE_UP if direction == "UP" else AFRR_PRICE_DOWN
                row["price_source"] = "estimate"
            return row

        def load(direction: str) -> tuple[dict, bool]:
            try:
                return dict(db["slots"].get(slot_id(key, market, direction, ramp))), True
            except NotFoundError:
                return new_row(direction), False

        rows = {}   # direction → (row, exists)

        # Activity and requested energy: the commanded direction
        row, exists = rows[signal] = load(signal)
        end_time = datetime.fromisoformat(row["end"])
        if exists:
            # Count only time this direction was commanded: after a flip and back, the gap since
            # this row's last tick is not added (capped at about one tick)
            since_last_s = (now - end_time).total_seconds()
            row["active_s"] = (row.get("active_s") or 0.0) + (since_last_s if since_last_s <= 20 else 10.0)
        else:
            row["active_s"] = seconds or 10.0
        start_time = datetime.fromisoformat(row["start"])
        row.update({
            "end": now.isoformat(),
            "duration_min": round(row["active_s"] / 60),
            "cancelled": now < (slot_end_time - timedelta(seconds=11)),
            "was_backup": (start_time - timeslot).total_seconds() >= 15,
            "requested_kwh": (row.get("requested_kwh") or 0.0) + (requested_w or 0.0) * seconds / 3_600_000.0,
            "baseline_w": baseline_w,
        })

        # Energy: the direction of the deviation from the baseline
        direction = deviation_direction(deviation_kwh) or signal
        if direction not in rows:
            rows[direction] = load(direction)
        row = rows[direction][0]
        row.update({
            "energy_kwh": (row.get("energy_kwh") or 0.0) + abs(deviation_kwh),
            "grid_kwh": (row.get("grid_kwh") or 0.0) + net_kwh,
            "grid_import_kwh": (row.get("grid_import_kwh") or 0.0) + max(0.0, net_kwh),
            "grid_export_kwh": (row.get("grid_export_kwh") or 0.0) + max(0.0, -net_kwh),
            "metered_s": (row.get("metered_s") or 0.0) + seconds,
            "baseline_import_kwh": (row.get("baseline_import_kwh") or 0.0) + max(0.0, baseline_kwh),
            "baseline_export_kwh": (row.get("baseline_export_kwh") or 0.0) + max(0.0, -baseline_kwh),
        })

        nps = self._nordpool_price(datetime.fromisoformat(price_slot)) if live else None
        for row, exists in rows.values():
            for k in ("energy_kwh", "grid_kwh", "grid_import_kwh", "grid_export_kwh", "baseline_import_kwh",
                      "baseline_export_kwh", "requested_kwh"):
                row[k] = round(row.get(k) or 0.0, 5)
            row["metered_s"] = round(row.get("metered_s") or 0.0, 1)
            row["active_s"] = round(row.get("active_s") or 0.0, 1)
            requested = row["requested_kwh"]
            row["delivery_pct"] = round(row["energy_kwh"] / requested * 100.0, 1) if requested > 0 else None
            if nps is not None and row.get("nordpool_price") is None:
                row["nordpool_price"] = nps
            if exists:
                db["slots"].update(row["id"], row, alter=True)
            else:
                db["slots"].insert(row, pk="id", alter=True)

    def _nordpool_price(self, slot_start: datetime) -> float | None:
        """Nord Pool price (€/kWh) for a slot from the HA Nord Pool sensor's attributes."""
        try:
            attrs = (get_entity(SENSOR_NORDPOOL) or {}).get("attributes", {})
            for p in (attrs.get("raw_today", []) or []) + (attrs.get("raw_tomorrow", []) or []):
                if datetime.fromisoformat(p["start"]) <= slot_start < datetime.fromisoformat(p["end"]):
                    return round(p["value"], 5)
        except Exception as e:
            print(f"❌ Failed to fetch Nordpool price: {e}")
        return None


live_tracker = Tracker()

def write_current_timeslot():
    live_tracker.tick(datetime.now(tz).replace(microsecond=0))

# Scheduler is started by FastAPI (api.py)
scheduler = BackgroundScheduler()
scheduler.add_job(write_current_timeslot, 'interval', seconds=10, max_instances=1, coalesce=True)
scheduler.add_job(cleanup_zero_min_rows, 'interval', minutes=1, max_instances=1, coalesce=True)