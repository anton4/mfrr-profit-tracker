# qw_report.py — import Qilowatt (KratTrade) CSV reports as the official reference
#
# Two report types, recognised by their header:
#   revenue: "Trader ()","Start","End","BRP Compensation (EUR)","UP Regulated Energy (kWh)",...
#   signals: "Aggregator ()","Start","End","Duration (min)","Direction ()",...,"Market ()",...
# Timestamps are local (Europe/Tallinn) time.
import csv
import io
from datetime import datetime

import pytz
from sqlite_utils import Database

import mffr_price_updater
import price_settings
from main import DB_PATH

tz = pytz.timezone("Europe/Tallinn")


def _local(value: str) -> datetime:
    return tz.localize(datetime.strptime(value.strip(), "%Y-%m-%d %H:%M:%S"))


def _num(value: str) -> float | None:
    value = (value or "").strip()
    return float(value) if value else None


def _col(row: dict, prefix: str):
    """Value of the column whose header starts with prefix (headers carry unit suffixes)."""
    for k, v in row.items():
        if k and k.startswith(prefix):
            return v
    return None


def import_report(content: bytes, db: Database | None = None) -> dict:
    db = db or Database(DB_PATH)
    db.conn.execute("PRAGMA busy_timeout=5000;")
    text = content.decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    header = text.split("\n", 1)[0]
    if header.startswith('"Trader') or header.startswith("Trader"):
        return _import_revenue(db, rows)
    if header.startswith('"Aggregator') or header.startswith("Aggregator"):
        return _import_signals(db, rows)
    raise ValueError("Unknown CSV: expected a Qilowatt balancing revenue or signals report")


def _import_revenue(db: Database, rows: list[dict]) -> dict:
    records = []
    for r in rows:
        if not r.get("Start"):
            continue   # "Total:" line
        start = _local(r["Start"])
        share_pct = _num(_col(r, "Revenue %")) or 0.0
        records.append({
            "timeslot": start.isoformat(),
            "brp_eur": _num(_col(r, "BRP Compensation")),
            "up_kwh": _num(_col(r, "UP Regulated Energy")),
            "up_revenue_eur": _num(_col(r, "UP Revenue (")),
            "up_net_eur": _num(_col(r, "UP Revenue −")),
            "down_kwh": _num(_col(r, "DOWN Regulated Energy")),
            "down_revenue_eur": _num(_col(r, "DOWN Revenue (")),
            "down_net_eur": _num(_col(r, "DOWN Revenue +")),
            "total_kwh": _num(_col(r, "Total Regulated Energy")),
            "total_revenue_eur": _num(_col(r, "Total Revenue (")),
            "total_net_eur": _num(_col(r, "Total Revenue BRP")),
            "share_pct": share_pct,
            "share_eur": _num(_col(r, "Total Revenue Share")),
        })
    db["qw_report_slots"].upsert_all(records, pk="timeslot", alter=True)
    return {"type": "revenue", "slots": len(records),
            "from": min((r["timeslot"] for r in records), default=None),
            "to": max((r["timeslot"] for r in records), default=None)}


def _import_signals(db: Database, rows: list[dict]) -> dict:
    records = []
    for r in rows:
        start = _local(r["Start"])
        records.append({
            "id": f'{start.isoformat()}_{_col(r, "Requested power")}_{_col(r, "Market")}',
            "start": start.isoformat(),
            "end": _local(r["End"]).isoformat(),
            "duration_min": _num(_col(r, "Duration")),
            "direction": _col(r, "Direction"),
            "requested_w": _num(_col(r, "Requested power")),
            "requested_mode": _col(r, "Requested mode"),
            "baseline_w": _num(_col(r, "Baseline power")),
            "grid_w": _num(_col(r, "Grid power")),
            "grid_kwh": _num(_col(r, "Grid kWh")),
            "battery_w": _num(_col(r, "Battery power")),
            "battery_kwh": _num(_col(r, "Battery kWh")),
            "market": _col(r, "Market") or None,
            "market_price": _num(_col(r, "Market Price")),
            "nps": _num(_col(r, "NPS")),
        })
    db["qw_report_signals"].upsert_all(records, pk="id", alter=True)
    corrected = _correct_markets(db, records)
    return {"type": "signals", "signals": len(records), "rows_market_corrected": corrected,
            "from": min((r["start"] for r in records), default=None),
            "to": max((r["start"] for r in records), default=None)}


def _correct_markets(db: Database, signals: list[dict]) -> int:
    """Set the market of tracker rows from the report where it disagrees with the timing rule.

    A slot is only corrected when the report shows a single market for it.
    """
    if "slots" not in db.table_names() or "market" not in db["slots"].columns_dict:
        return 0
    slot_markets = {}
    for s in signals:
        if not s["market"]:
            continue
        start = datetime.fromisoformat(s["start"])
        slot = start.replace(minute=(start.minute // 15) * 15, second=0).isoformat()
        slot_markets.setdefault(slot, set()).add(s["market"])
    corrected = 0
    for slot, markets in slot_markets.items():
        if len(markets) != 1:
            continue
        market = next(iter(markets))
        for row in db["slots"].rows_where("timeslot = ? AND market IS NOT NULL AND market != ?", [slot, market]):
            update = {"market": market, "profit": None, "net_total": None}
            if market == "AFRR":
                prices = price_settings.at(row["timeslot"])
                update.update(mffr_price=prices["afrr_price_up" if row["signal"] == "UP" else "afrr_price_down"],
                              price_source="estimate", price_timeslot=row["timeslot"])
            else:
                update.update(mffr_price=None, price_source=None)   # BTD sync fills it
            db["slots"].update(row["id"], update)
            corrected += 1
    if corrected:
        mffr_price_updater.sync_status["last_sync_at"] = None   # let the BTD sync run right away
    return corrected


def report_slots(start: str | None, end: str | None) -> list[dict]:
    db = Database(DB_PATH)
    if "qw_report_slots" not in db.table_names():
        return []
    where, args = [], []
    if start:
        where.append("timeslot >= ?"); args.append(start)
    if end:
        where.append("timeslot <= ?"); args.append(end)
    return list(db["qw_report_slots"].rows_where(" AND ".join(where) or None, args, order_by="timeslot"))
