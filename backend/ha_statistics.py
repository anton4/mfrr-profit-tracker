# ha_statistics.py — hourly payout statistics for the Home Assistant Energy dashboard
#
# The payout is written as external statistics (recorder/import_statistics over the WebSocket API),
# not derived from a sensor's state changes. Importing an hour again overwrites it, so later
# corrections land in the hours they belong to: mFRR prices published an hour after the slot,
# Volton aFRR prices replacing the estimate, backfills, and imported Qilowatt revenue reports.
import json
import os
from datetime import datetime, timedelta, timezone

import pytz
from sqlite_utils import Database
from websockets.sync.client import connect

import config
from ha import HA_TOKEN, HA_URL

tz = pytz.timezone("Europe/Tallinn")

SOURCE = "mfrr_tracker"
PAYOUT_ID = f"{SOURCE}:activation_payout"
ZERO_ENERGY_ID = f"{SOURCE}:energy_zero"
CHUNK = 500

_METADATA = {
    PAYOUT_ID: {"name": "mFRR activation payout", "unit_of_measurement": "EUR", "unit_class": None},
    ZERO_ENERGY_ID: {"name": "mFRR zero energy", "unit_of_measurement": "kWh", "unit_class": "energy"},
}

_pushed: dict[str, float] = {}     # hour start (UTC ISO) → payout sum last pushed; empty after a start
_extended_metadata = False         # newer HA versions want mean_type / unit_class


def ws_url() -> str:
    if os.getenv("HA_WS_URL"):
        return os.environ["HA_WS_URL"]
    base = HA_URL.rstrip("/")
    if base == "http://supervisor/core":
        return "ws://supervisor/core/websocket"
    return base.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + "/api/websocket"


def slot_payouts(db: Database) -> tuple[dict[str, float], int, int]:
    """Payout per 15-minute slot (local ISO): the official Qilowatt report share where a report
    covers the slot, otherwise the tracker's own activation payout. Also the official and
    estimated slot counts."""
    estimate: dict[str, float] = {}
    if "slots" in db.table_names():
        for r in db["slots"].rows_where("profit IS NOT NULL", select="profit, price_timeslot, timeslot"):
            # price_timeslot: an mFRR ramp minute belongs to the next quarter, as Kratt books it
            slot = r["price_timeslot"] or r["timeslot"]
            estimate[slot] = estimate.get(slot, 0.0) + r["profit"]
    official: dict[str, float] = {}
    if "qw_report_slots" in db.table_names():
        for r in db["qw_report_slots"].rows:
            share = r.get("share_eur")
            if share is None:
                pct = (r.get("share_pct") or 80.0) / 100.0
                share = ((r.get("up_net_eur") or 0.0) + (r.get("down_net_eur") or 0.0)) * pct
            official[r["timeslot"]] = share
    payouts = {**estimate, **official}
    return payouts, len(official), len(set(estimate) - set(official))


def hourly_series(db: Database, now: datetime | None = None) -> dict[str, float]:
    """Hour start (UTC ISO) → cumulative payout, for every hour from the first payout to now."""
    payouts, _, _ = slot_payouts(db)
    if not payouts:
        return {}
    hourly: dict[datetime, float] = {}
    for slot, eur in payouts.items():
        hour = datetime.fromisoformat(slot).astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        hourly[hour] = hourly.get(hour, 0.0) + eur
    end = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    hour, total, series = min(hourly), 0.0, {}
    while hour <= max(end, max(hourly)):
        total += hourly.get(hour, 0.0)
        series[hour.isoformat()] = round(total, 5)
        hour += timedelta(hours=1)
    return series


def _metadata(statistic_id: str) -> dict:
    meta = {"has_mean": False, "has_sum": True, "name": _METADATA[statistic_id]["name"],
            "source": SOURCE, "statistic_id": statistic_id,
            "unit_of_measurement": _METADATA[statistic_id]["unit_of_measurement"]}
    if _extended_metadata:
        meta.pop("has_mean")
        meta.update(mean_type=0, unit_class=_METADATA[statistic_id]["unit_class"])
    return meta


class _Connection:
    def __init__(self):
        self.ws = connect(ws_url(), open_timeout=10, max_size=None)
        self.next_id = 1
        if json.loads(self.ws.recv(timeout=10)).get("type") != "auth_required":
            raise RuntimeError("Unexpected WebSocket greeting from Home Assistant")
        self.ws.send(json.dumps({"type": "auth", "access_token": HA_TOKEN}))
        reply = json.loads(self.ws.recv(timeout=10))
        if reply.get("type") != "auth_ok":
            raise RuntimeError(f"Home Assistant WebSocket auth failed: {reply.get('message', reply)}")

    def call(self, message: dict) -> dict:
        message = {**message, "id": self.next_id}
        self.next_id += 1
        self.ws.send(json.dumps(message))
        while True:
            reply = json.loads(self.ws.recv(timeout=60))
            if reply.get("id") == message["id"] and reply.get("type") == "result":
                return reply

    def close(self):
        self.ws.close()


def statistics_during_period(statistic_ids: list[str], start: datetime, end: datetime, period: str) -> dict:
    """Home Assistant's long-term statistics (mean, in W) per id: {id: [(start, mean), …]}.
    period: "5minute" (kept as long as the recorder history) or "hour" (kept indefinitely)."""
    conn = _Connection()
    try:
        reply = conn.call({
            "type": "recorder/statistics_during_period",
            "start_time": start.astimezone(timezone.utc).isoformat(),
            "end_time": end.astimezone(timezone.utc).isoformat(),
            "statistic_ids": statistic_ids, "period": period, "types": ["mean"], "units": {"power": "W"},
        })
    finally:
        conn.close()
    if not reply.get("success"):
        raise RuntimeError(f"statistics_during_period failed: {reply.get('error')}")
    result = {}
    for statistic_id, rows in (reply.get("result") or {}).items():
        result[statistic_id] = [(datetime.fromtimestamp(r["start"] / 1000, timezone.utc) if isinstance(r["start"], (int, float))
                                 else datetime.fromisoformat(r["start"]), r.get("mean")) for r in rows]
    return result


def _import(conn: _Connection, statistic_id: str, stats: list[dict]):
    global _extended_metadata
    for i in range(0, len(stats), CHUNK):
        chunk = stats[i:i + CHUNK]
        reply = conn.call({"type": "recorder/import_statistics", "metadata": _metadata(statistic_id), "stats": chunk})
        if not reply.get("success") and not _extended_metadata and reply.get("error", {}).get("code") == "invalid_format":
            _extended_metadata = True
            reply = conn.call({"type": "recorder/import_statistics", "metadata": _metadata(statistic_id), "stats": chunk})
        if not reply.get("success"):
            raise RuntimeError(f"import_statistics {statistic_id} failed: {reply.get('error')}")


def push_statistics(now: datetime | None = None):
    if not config.PUBLISH_SENSORS:
        return
    db = Database(config.DB_PATH)
    try:
        db.conn.execute("PRAGMA busy_timeout=5000;")
        series = hourly_series(db, now)
    finally:
        db.conn.close()
    if not series:
        return
    # Hours pushed earlier but now before the first payout (e.g. after a backfill) go back to 0
    for h in sorted(h for h in _pushed if h < min(series)):
        series[h] = 0.0
    series = dict(sorted(series.items()))

    # Cumulative sums: a change in one hour shifts every later hour, so send from the first change
    hours = list(series)
    changed = next((h for h in hours if _pushed.get(h) != series[h]), None)
    if changed is None:
        return
    send = hours[hours.index(changed):]

    try:
        conn = _Connection()
    except Exception as e:
        print(f"❌ Statistics push: can't connect to Home Assistant ({ws_url()}): {e}")
        return
    try:
        _import(conn, PAYOUT_ID, [{"start": h, "state": series[h], "sum": series[h]} for h in send])
        _import(conn, ZERO_ENERGY_ID, [{"start": h, "state": 0.0, "sum": 0.0} for h in send])
    except Exception as e:
        print(f"❌ Statistics push: {e}")
        return
    finally:
        conn.close()
    _pushed.clear()
    _pushed.update(series)
    print(f"📈 Pushed payout statistics for {len(send)} hour(s) from {changed}")
