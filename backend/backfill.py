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
import entsoe_cbmp
import profit_calc
import sensors
from history import HistoryStates, current_units, fetch_history, oldest_history
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


# The first slot a backfill may start: history_from plus this much (the meter's first reading and the 60 s
# pre-signal baseline), rounded up to a quarter
HISTORY_LEAD = timedelta(minutes=2)
RANGE_TTL = timedelta(hours=1)        # the recorder purges once a night
RANGE_ERROR_TTL = timedelta(minutes=1)

_range_cache = {"key": None, "at": None, "value": None}
_range_lock = threading.Lock()


def _needed_entities() -> list[str]:
    """The sensors a backfill replays, as run_backfill reads them."""
    sensor = sensors.current()
    entities = [sensor["source"], sensor["mode"], *(sensor["grid_power"] or [])]
    if sensor["powerlimit"]:
        entities.append(sensor["powerlimit"])
    return [e for e in entities if e]


def _ceil_slot(dt: datetime) -> datetime:
    floor = _floor_slot(dt)
    return floor if floor == dt.astimezone(tz) else _floor_slot(floor + SLOT)


def _fmt(dt: datetime) -> str:
    return dt.astimezone(tz).strftime("%d.%m.%Y %H:%M")


def available_range(force: bool = False) -> dict:
    """The slots a backfill can rebuild: from the oldest history Home Assistant keeps for the tracker's sensors to
    the start of the current slot. Cached for RANGE_TTL per sensor list (RANGE_ERROR_TTL after an error)."""
    now = datetime.now(tz)
    entities = _needed_entities()
    key = tuple(entities)
    with _range_lock:
        cached, at = _range_cache["value"], _range_cache["at"]
        ttl = RANGE_ERROR_TTL if cached and cached["error"] else RANGE_TTL
        if not force and cached and _range_cache["key"] == key and now - at < ttl:
            return {**cached, "latest": _floor_slot(now).isoformat()}
        value = {"history_from": None, "earliest": None, "limited_by": [], "missing": [],
                 "checked_at": now.isoformat(), "error": None}
        if not entities:
            value["error"] = "Set the Qilowatt and grid power sensors in the add-on configuration first"
        else:
            try:
                found = oldest_history(entities, now)
                value.update(limited_by=found["limited_by"], missing=found["missing"])
                if found["history_from"] is not None:
                    value["history_from"] = found["history_from"].astimezone(tz).isoformat()
                    value["earliest"] = _ceil_slot(found["history_from"] + HISTORY_LEAD).isoformat()
            except Exception as e:
                value["error"] = f"Couldn't read Home Assistant's history: {e}"
        _range_cache.update(key=key, at=now, value=value)
        return {**value, "latest": _floor_slot(now).isoformat()}


def check_start(start: datetime) -> None:
    """Refuse a start before the oldest history Home Assistant keeps (or with no history for a sensor). When that
    couldn't be read, the backfill goes ahead and says if the history is missing."""
    r = available_range()
    if r["missing"]:
        raise ValueError(f"Home Assistant records no history for {', '.join(r['missing'])}: check the "
                         "recorder's include and exclude settings")
    if r["earliest"] and _floor_slot(start) < datetime.fromisoformat(r["earliest"]):
        raise ValueError(f"Home Assistant keeps history from {_fmt(datetime.fromisoformat(r['history_from']))}, "
                         f"so a backfill can start at {_fmt(datetime.fromisoformat(r['earliest']))} at the earliest")


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
    check_start(start)

    sensor = sensors.current()
    grid_power = sensor["grid_power"]
    if not sensor["source"] or not sensor["mode"] or not grid_power:
        raise ValueError("Set the Qilowatt and grid power sensors in the add-on configuration first")
    entities = [sensor["source"], sensor["mode"], *grid_power]
    if sensor["powerlimit"]:
        entities.append(sensor["powerlimit"])

    # 1) HA history (warm-up included)
    progress("fetching", 0)
    warm_start = start - WARMUP
    changes = fetch_history(entities, warm_start, end, lambda f: progress("fetching", 30 * f))
    if not changes.get(sensor["source"]) or not all(changes.get(e) for e in grid_power):
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
        # aFRR market price (CBMP) for comparison, when an ENTSO-E token is configured
        if afrr_rows and entsoe_cbmp.configured():
            try:
                entsoe_cbmp.fill_rows(db, afrr_rows)
            except Exception as e:
                print(f"⚠️ ENTSO-E CBMP unavailable: {e}")

    progress("done", 100)
    return {"from": start.isoformat(), "to": end.isoformat(), "slots_written": len(rows)}


# ---- background job (one at a time) for the API/UI ----

_lock = threading.Lock()
status = {"state": "idle", "from": None, "to": None, "phase": None, "progress": 0,
          "slots_written": None, "message": None, "started_at": None, "finished_at": None}


def start_job(start: datetime, end: datetime) -> dict:
    check_start(start)   # answer right away instead of from the background job
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
