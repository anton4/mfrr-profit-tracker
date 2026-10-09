# price_settings.py — prices and fees over time.
#
# The add-on options hold the current Kratt share, aFRR estimates and electricity fees. When they
# change, the new values apply either to all history or from the current slot on: chosen when
# saving in the tracker's UI, and "from now on" for changes made in Home Assistant's Configuration
# tab. Each change is stored as a version with the time it applies from; every slot is calculated
# with the version in effect at its start.
import json
import os
from bisect import bisect_right
from datetime import datetime, timezone

import pytz
from sqlite_utils import Database

import config
import fees

DB_PATH = config.DB_PATH
tz = pytz.timezone("Europe/Tallinn")
TABLE = "price_settings"
PENDING_KEY = "price_scope"   # settings row: the scope chosen when saving in the UI

# Current values from the add-on options (.env standalone). aFRR prices are estimates (€/MWh) used
# until Volton or a Qilowatt report gives the real figure; the defaults are the rates implied by
# Kratt reports (UP revenue ≈ 440 €/MWh, DOWN ≈ 530 €/MWh paid for absorbing → price −530).
CURRENT = {
    "kratt_share": float(os.getenv("KRATT_SHARE", "0.20")),   # 0.20 = Kratt keeps 20 %
    "afrr_price_up": float(os.getenv("AFRR_PRICE_UP_EUR_MWH", "440")),
    "afrr_price_down": float(os.getenv("AFRR_PRICE_DOWN_EUR_MWH", "-530")),
    "fees": fees.get_fees(),
}

ALL_HISTORY = datetime.min.replace(tzinfo=timezone.utc)
_starts: list[datetime] = []   # when each version applies from, ascending
_values: list[dict] = []


def _slot_start(dt: datetime) -> datetime:
    dt = dt.astimezone(tz)
    return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)


def set_pending(scope: str, db: Database | None = None) -> None:
    """Remember how the next price change applies ("all" or "now"); read on the next start."""
    if scope not in ("all", "now"):
        raise ValueError("scope must be 'all' or 'now'")
    db = db or Database(DB_PATH)
    db["settings"].upsert({"key": PENDING_KEY, "value": json.dumps(
        {"scope": scope, "at": datetime.now(tz).isoformat()})}, pk="key")


def _pop_pending(db: Database) -> dict | None:
    if "settings" not in db.table_names():
        return None
    try:
        pending = json.loads(db["settings"].get(PENDING_KEY)["value"])
    except Exception:
        return None
    db["settings"].delete(PENDING_KEY)
    return pending


def _describe(old: dict, new: dict) -> str:
    changes = [f"{k} {old.get(k)} → {new[k]}" for k in ("kratt_share", "afrr_price_up", "afrr_price_down")
               if old.get(k) != new[k]]
    changes += [f"fee {k} {old['fees'].get(k)} → {v}" for k, v in new["fees"].items()
                if old.get("fees", {}).get(k) != v]
    return ", ".join(changes)


def reconcile(db: Database | None = None) -> None:
    """On start: store the current prices and fees as a new version if they changed."""
    db = db or Database(DB_PATH)
    pending = _pop_pending(db)
    versions = list(db[TABLE].rows_where(order_by="id")) if TABLE in db.table_names() else []
    last = json.loads(versions[-1]["settings"]) if versions else None
    if last != CURRENT:
        # The first version covers all history, as before prices had versions
        scope = "all" if not versions else (pending or {}).get("scope", "now")
        at = datetime.fromisoformat(pending["at"]) if pending else datetime.now(tz)
        effective_from = None if scope == "all" else _slot_start(at).isoformat()
        with db.conn:
            if scope == "all":
                db[TABLE].delete_where()
            db[TABLE].insert({"effective_from": effective_from, "recorded_at": datetime.now(tz).isoformat(),
                              "scope": scope, "settings": json.dumps(CURRENT)}, pk="id", alter=True)
        if last is not None:
            change = _describe(last, CURRENT)
            if scope == "all":
                recalculate_history(db)
                print(f"💶 Prices and fees changed ({change}): applied to all history, recalculating")
            else:
                print(f"💶 Prices and fees changed ({change}): applied from {effective_from}")
    _load(db)


def recalculate_history(db: Database) -> None:
    """Current aFRR estimates on every estimated row, and profits recalculated by profit_calc."""
    if "slots" not in db.table_names():
        return
    with db.conn:
        for signal, key in (("UP", "afrr_price_up"), ("DOWN", "afrr_price_down")):
            db.conn.execute("UPDATE slots SET mffr_price = ? WHERE market = 'AFRR' AND price_source = 'estimate' "
                            "AND signal = ?", [CURRENT[key], signal])
        db.conn.execute("UPDATE slots SET profit = NULL, net_total = NULL")


def _load(db: Database) -> None:
    global _starts, _values
    rows = list(db[TABLE].rows_where(order_by="id")) if TABLE in db.table_names() else []
    start = lambda r: datetime.fromisoformat(r["effective_from"]) if r["effective_from"] else ALL_HISTORY
    ordered = sorted(rows, key=lambda r: (start(r), r["id"]))
    _starts = [start(r) for r in ordered]
    _values = [json.loads(r["settings"]) for r in ordered]


def at(timeslot) -> dict:
    """Prices and fees in effect for the slot starting at `timeslot` (ISO string or datetime)."""
    if not _values:
        return CURRENT
    when = timeslot if isinstance(timeslot, datetime) else datetime.fromisoformat(timeslot)
    i = bisect_right(_starts, when) - 1
    return _values[max(i, 0)]
