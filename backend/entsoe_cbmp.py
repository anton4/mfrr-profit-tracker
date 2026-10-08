# entsoe_cbmp.py — Estonian aFRR cross-border marginal prices (CBMP, 4-second PICASSO prices)
# from the ENTSO-E Transparency Platform: "Cross Border Marginal Prices (CBMPs) for aFRR Central
# Selection" (documentType A84, processType A67, businessType A96). Needs a free ENTSO-E API
# security token (ENTSOE_TOKEN).
#
# Shown next to the aFRR income estimate as the market price for comparison: Kratt's reports pay
# 1.3–3× this price, so it is not used for income.
import os
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import pytz
import requests
from sqlite_utils import Database

import config

DB_PATH = config.DB_PATH
tz = pytz.timezone("Europe/Tallinn")

ENTSOE_API = "https://web-api.tp.entsoe.eu/api"
ENTSOE_TOKEN = os.getenv("ENTSOE_TOKEN", "")
AREA = os.getenv("ENTSOE_CBMP_AREA", "10Y1001A1001A39I")    # Estonia (Elering)
SOURCE = "ENTSO-E (PICASSO)"
TODAY_REFRESH = timedelta(hours=1)
DIRECTIONS = {"A01": "UP", "A02": "DOWN"}


def configured() -> bool:
    return bool(ENTSOE_TOKEN)


def _ensure_schema(db: Database):
    # Cache written by the removed newday.ee integration (no "source" column): drop it and
    # forget the market prices taken from it, so only ENTSO-E data is shown.
    if "afrr_cbmp_days" in db.table_names() and "source" not in db["afrr_cbmp_days"].columns_dict:
        db["afrr_cbmp"].drop(ignore=True)
        db["afrr_cbmp_days"].drop(ignore=True)
        if "slots" in db.table_names() and "cbmp_avg" in db["slots"].columns_dict:
            with db.conn:
                db.conn.execute("UPDATE slots SET cbmp_avg = NULL, cbmp_points = NULL")
        print("🧹 Removed aFRR market prices cached from newday.ee")
    if "afrr_cbmp" not in db.table_names():
        db["afrr_cbmp"].create({"ts": int, "dir": str, "price": float}, pk=("ts", "dir"))
    if "afrr_cbmp_days" not in db.table_names():
        db["afrr_cbmp_days"].create({"day": str, "fetched_at": str, "points": int, "source": str}, pk="day")


def migrate(db: Database | None = None):
    """Run at startup: set up the cache and drop anything left from newday.ee."""
    _ensure_schema(db or Database(DB_PATH))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el, name):
    return next((c for c in el if _local(c.tag) == name), None)


def _text(el, path: str) -> str | None:
    """Text of a nested child by local names, e.g. 'timeInterval/start'."""
    for name in path.split("/"):
        el = _child(el, name) if el is not None else None
    return el.text.strip() if el is not None and el.text else None


def _seconds(resolution: str) -> int:
    """ISO 8601 duration like PT4S, PT1M, PT15M → seconds."""
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", resolution or "")
    if not m or not any(m.groups()):
        raise ValueError(f"Unsupported resolution {resolution!r}")
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def parse(xml_text: str) -> list[tuple[int, str, float]]:
    """[(unix_ts, 'UP'|'DOWN', €/MWh)] from an A84 balancing document. Raises on an
    acknowledgement (error) document, except 'no matching data', which means no prices."""
    root = ET.fromstring(xml_text)
    if "Acknowledgement" in _local(root.tag):
        reason = " ".join(t.text for t in root.iter() if _local(t.tag) == "text" and t.text)
        if "No matching data" in reason:
            return []
        raise RuntimeError(f"ENTSO-E: {reason or 'request rejected'}")
    points = []
    for series in (e for e in root.iter() if _local(e.tag) == "TimeSeries"):
        direction = DIRECTIONS.get(_text(series, "flowDirection.direction") or "")
        if not direction:
            continue
        for period in (e for e in series if _local(e.tag) == "Period"):
            start = datetime.fromisoformat(_text(period, "timeInterval/start").replace("Z", "+00:00"))
            step = _seconds(_text(period, "resolution"))
            for point in (e for e in period if _local(e.tag) == "Point"):
                price = _text(point, "activation_Price.amount") or _text(point, "price.amount")
                if price is None:
                    continue
                ts = start + timedelta(seconds=step * (int(_text(point, "position")) - 1))
                points.append((int(ts.timestamp()), direction, float(price)))
    return points


def fetch_day(day: date) -> list[tuple[int, str, float]]:
    """CBMP for one Tallinn day from the ENTSO-E API. Raises on HTTP or API errors."""
    if not configured():
        raise RuntimeError("ENTSO-E API token not set (ENTSOE_TOKEN)")
    start = tz.localize(datetime(day.year, day.month, day.day)).astimezone(timezone.utc)
    end = tz.localize(datetime.combine(day + timedelta(days=1), datetime.min.time())).astimezone(timezone.utc)
    resp = requests.get(ENTSOE_API, params={
        "securityToken": ENTSOE_TOKEN,
        "documentType": "A84",
        "processType": "A67",
        "businessType": "A96",
        "Standard_MarketProduct": "A01",
        "controlArea_Domain": AREA,
        "periodStart": start.strftime("%Y%m%d%H%M"),
        "periodEnd": end.strftime("%Y%m%d%H%M"),
    }, timeout=120)
    if resp.status_code == 401:
        raise RuntimeError("ENTSO-E rejected the API token (ENTSOE_TOKEN)")
    if resp.status_code >= 400 and "Acknowledgement" not in resp.text:
        resp.raise_for_status()
    return parse(resp.text)


def ensure_days(days: set[date], db: Database | None = None) -> int:
    """Fetch the CBMP of the given days into the cache. A past day is fetched once; today at most
    hourly. Returns the number of days fetched."""
    db = db or Database(DB_PATH)
    _ensure_schema(db)
    now = datetime.now(tz)
    fetched = 0
    for day in sorted(days):
        if day > now.date():
            continue
        try:
            log = db["afrr_cbmp_days"].get(day.isoformat())
            last = datetime.fromisoformat(log["fetched_at"])
            settled = last.date() > day          # fetched after the day ended
            if settled or (day == now.date() and now - last < TODAY_REFRESH):
                continue
        except Exception:
            pass
        points = fetch_day(day)
        db["afrr_cbmp"].upsert_all(({"ts": t, "dir": d, "price": p} for t, d, p in points), pk=("ts", "dir"))
        db["afrr_cbmp_days"].upsert({"day": day.isoformat(), "fetched_at": now.isoformat(),
                                     "points": len(points), "source": SOURCE}, pk="day")
        fetched += 1
    return fetched


def cbmp_avg(db: Database, direction: str, start: datetime, end: datetime) -> tuple[float | None, int]:
    """Mean CBMP (€/MWh) of the 4-second prices published in [start, end] and how many there were.
    A gap in the data means no activation, so no price is held across it."""
    _ensure_schema(db)
    row = db.execute("SELECT AVG(price), COUNT(*) FROM afrr_cbmp WHERE dir = ? AND ts >= ? AND ts <= ?",
                     [direction, int(start.timestamp()), int(end.timestamp())]).fetchone()
    return (round(row[0], 2) if row[0] is not None else None), row[1]


def row_window(row: dict) -> tuple[datetime, datetime]:
    """The seconds a row's direction was active (at least one tick)."""
    start = datetime.fromisoformat(row["start"])
    end = max(datetime.fromisoformat(row["end"]), start + timedelta(seconds=10))
    return start - timedelta(seconds=10), end        # include the tick interval before the first tick


def fill_rows(db: Database, rows: list[dict]) -> int:
    """Fetch the needed days and set cbmp_avg / cbmp_points on aFRR rows. Returns rows updated."""
    days = set()
    for r in rows:
        a, b = row_window(r)
        days.update({a.astimezone(tz).date(), b.astimezone(tz).date()})
    ensure_days(days, db)
    updated = 0
    for r in rows:
        avg, n = cbmp_avg(db, r["signal"], *row_window(r))
        db["slots"].update(r["id"], {"cbmp_avg": avg, "cbmp_points": n}, alter=True)
        updated += 1
    return updated
