# backfill.py — rebuild slot rows for a past period by replaying Home Assistant history
# through the same tick logic as the live tracker (main.Tracker).
#
# CLI: python backfill.py --from 2026-09-28 --to 2026-10-05
import argparse
import threading
from bisect import bisect_right
from datetime import datetime, timedelta, timezone

import pytz
import requests
from sqlite_utils import Database

import mffr_price_updater
import profit_calc
from ha import SENSOR_GRID_POWER, SENSOR_MODE, SENSOR_POWERLIMIT, SENSOR_SOURCE
from history import HistoryStates, current_units, fetch_history
from main import DB_PATH, Tracker

tz = pytz.timezone("Europe/Tallinn")

TICK = timedelta(seconds=10)          # same cadence as the live tracker
WARMUP = timedelta(minutes=30)        # primes the meter and the pre-signal baseline
SLOT = timedelta(minutes=15)
ELERING_NPS_URL = "https://dashboard.elering.ee/api/nps/price"


def _floor_slot(dt: datetime) -> datetime:
    local = dt.astimezone(tz)
    return local.replace(minute=(local.minute // 15) * 15, second=0, microsecond=0)


def parse_local(value: str) -> datetime:
    """ISO date/datetime; naive values are taken as Europe/Tallinn local time."""
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return tz.localize(dt) if dt.tzinfo is None else dt.astimezone(tz)


def _rows_in_range(db: Database, start: datetime, end: datetime) -> list[dict]:
    # timeslot strings carry the local offset, so compare parsed datetimes (DST-safe)
    rows = db["slots"].rows_where(
        "timeslot >= ? AND timeslot < ?",
        [(start - timedelta(days=1)).isoformat(), (end + timedelta(days=1)).isoformat()],
    )
    return [r for r in rows if start <= datetime.fromisoformat(r["timeslot"]) < end]


def fetch_nps_prices(start: datetime, end: datetime) -> list[tuple[datetime, float]]:
    """Estonian Nord Pool day-ahead prices from Elering: [(utc_start, €/kWh)] sorted."""
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    resp = requests.get(ELERING_NPS_URL, params={
        "start": start.astimezone(timezone.utc).strftime(fmt),
        "end": end.astimezone(timezone.utc).strftime(fmt),
    }, timeout=30)
    resp.raise_for_status()
    prices = [(datetime.fromtimestamp(p["timestamp"], timezone.utc), p["price"] / 1000.0)
              for p in resp.json()["data"]["ee"]]
    return sorted(prices)


def run_backfill(start: datetime, end: datetime, progress=lambda phase, pct: None) -> dict:
    """Recompute all slot rows in [start, end) from HA history. Returns a summary."""
    start = _floor_slot(start)
    end = min(_floor_slot(end), _floor_slot(datetime.now(tz)))   # never touch the live slot
    if start >= end:
        raise ValueError("Empty range: 'to' must be after 'from' and before the current slot")

    entities = [SENSOR_SOURCE, SENSOR_MODE, *SENSOR_GRID_POWER]
    if SENSOR_POWERLIMIT:
        entities.append(SENSOR_POWERLIMIT)

    # 1) HA history (warm-up included)
    progress("fetching", 0)
    warm_start = start - WARMUP
    changes = fetch_history(entities, warm_start, end, lambda f: progress("fetching", 30 * f))
    if not changes.get(SENSOR_SOURCE) or not all(changes.get(e) for e in SENSOR_GRID_POWER):
        raise RuntimeError("No Home Assistant history for this range. The recorder keeps "
                           "purge_keep_days of history (10 days by default).")
    states = HistoryStates(changes, current_units(entities))

    # 2) Recompute: drop existing rows in the range, then replay tick by tick
    db = Database(DB_PATH)
    db.conn.execute("PRAGMA busy_timeout=5000;")
    for row in _rows_in_range(db, start, end):
        db["slots"].delete(row["id"])

    tracker = Tracker(store_baseline=False)
    t = warm_start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    total = (end_utc - t).total_seconds()
    ticks = 0
    while t < end_utc:
        now = t.astimezone(tz)          # local time with the correct DST offset
        states.now = t
        tracker.tick(now, fetch=states.fetch, write=now >= start, live=False, db=db)
        t += TICK
        ticks += 1
        if ticks % 360 == 0:            # every simulated hour
            progress("replaying", 30 + 60 * (1 - (end_utc - t).total_seconds() / total))

    # 3) Prices for the range (not limited by the live updater's lookback)
    progress("pricing", 90)
    rows = _rows_in_range(db, start, end)
    if rows:
        # mFRR from the Baltic Transparency Dashboard (ramp minutes use the next quarter: +15 min)
        mfrr_rows = [r for r in rows if (r.get("market") or "MFRR") == "MFRR"]
        if mfrr_rows:
            btd = mffr_price_updater.fetch_btd_prices(start, end + timedelta(minutes=15))
            mffr_price_updater.apply_mffr_prices(db, mfrr_rows, btd)
        # aFRR: Volton clearing price where published, else the estimate set by the tracker
        afrr_rows = [r for r in rows if r.get("market") == "AFRR"]
        if afrr_rows:
            try:
                volton = mffr_price_updater.fetch_volton_afrr_prices()
            except Exception as e:
                print(f"⚠️ Volton aFRR prices unavailable: {e}")
                volton = {}
            for r in afrr_rows:
                slot = datetime.fromisoformat(r.get("price_timeslot") or r["timeslot"]).astimezone(timezone.utc)
                price = volton.get((slot, r["signal"]))
                if price is not None:
                    db["slots"].update(r["id"], {"mffr_price": price, "price_source": "volton"})
        nps = fetch_nps_prices(start - timedelta(hours=1), end + timedelta(minutes=15))
        nps_times = [p[0] for p in nps]
        for row in rows:
            slot = datetime.fromisoformat(row.get("price_timeslot") or row["timeslot"])
            i = bisect_right(nps_times, slot) - 1
            if i >= 0:   # hourly (older) or 15-min prices: last price at or before the slot start
                db["slots"].update(row["id"], {"nordpool_price": round(nps[i][1], 5)})
        profit_calc.run_profit_calculation()

    progress("done", 100)
    return {"from": start.isoformat(), "to": end.isoformat(), "slots_written": len(rows)}


# ---- background job (one at a time) for the API/UI ----

_lock = threading.Lock()
status = {"state": "idle", "from": None, "to": None, "phase": None, "progress": 0,
          "slots_written": None, "message": None, "started_at": None, "finished_at": None}


def start_job(start: datetime, end: datetime) -> dict:
    if not _lock.acquire(blocking=False):
        raise RuntimeError("A backfill is already running")
    status.update(state="running", phase="starting", progress=0, slots_written=None, message=None,
                  started_at=datetime.now(tz).isoformat(), finished_at=None,
                  **{"from": start.isoformat(), "to": end.isoformat()})

    def progress(phase, pct):
        status.update(phase=phase, progress=round(pct))

    def run():
        try:
            result = run_backfill(start, end, progress)
            status.update(state="done", slots_written=result["slots_written"],
                          message=f"Rebuilt {result['slots_written']} slot row(s)",
                          **{"from": result["from"], "to": result["to"]})
        except Exception as e:
            status.update(state="error", message=str(e))
            print(f"❌ Backfill failed: {e}")
        finally:
            status["finished_at"] = datetime.now(tz).isoformat()
            _lock.release()

    threading.Thread(target=run, name="backfill", daemon=True).start()
    return dict(status)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rebuild mFRR slot rows from Home Assistant history")
    parser.add_argument("--from", dest="start", required=True, help="start (local date/time, e.g. 2026-09-28)")
    parser.add_argument("--to", dest="end", required=True, help="end (local date/time, exclusive)")
    args = parser.parse_args()
    result = run_backfill(parse_local(args.start), parse_local(args.end),
                          lambda phase, pct: print(f"… {phase} {pct:.0f}%"))
    print(f"✅ Backfill {result['from']} → {result['to']}: {result['slots_written']} slot row(s)")
