# newday.py — Estonian aFRR cross-border marginal prices (CBMP) per 4-second MTU from the
# newday.ee aFRR dashboard (login required; credentials from NEWDAY_USER / NEWDAY_PASSWORD).
#
# Shown next to the aFRR income estimate as the market price for comparison: Kratt's reports pay
# 1.3–3× this price, so it is not used for income.
import json
import os
import re
from datetime import date, datetime, timedelta

import pytz
import requests
from sqlite_utils import Database

DB_PATH = "data/mffr.db"
tz = pytz.timezone("Europe/Tallinn")

NEWDAY_URL = os.getenv("NEWDAY_URL", "https://srv.newday.ee/afrr_dashboard.php")
NEWDAY_USER = os.getenv("NEWDAY_USER", "")
NEWDAY_PASSWORD = os.getenv("NEWDAY_PASSWORD", "")
TODAY_REFRESH = timedelta(hours=1)

_PAYLOAD = re.compile(r'<script[^>]*id="rf-data"[^>]*>(.*?)</script>', re.S)


def configured() -> bool:
    return bool(NEWDAY_USER and NEWDAY_PASSWORD)


def _ensure_schema(db: Database):
    if "afrr_cbmp" not in db.table_names():
        db["afrr_cbmp"].create({"ts": int, "dir": str, "price": float}, pk=("ts", "dir"))
    if "afrr_cbmp_days" not in db.table_names():
        db["afrr_cbmp_days"].create({"day": str, "fetched_at": str, "points": int}, pk="day")


def fetch_day(day: date) -> list[tuple[int, str, float]]:
    """[(unix_ts, 'UP'|'DOWN', €/MWh)] for one Tallinn day. Raises on login or format errors."""
    if not configured():
        raise RuntimeError("newday.ee credentials not configured (NEWDAY_USER / NEWDAY_PASSWORD)")
    with requests.Session() as s:
        s.get(NEWDAY_URL, timeout=30)                       # session cookie
        s.post(NEWDAY_URL, data={"username": NEWDAY_USER, "password": NEWDAY_PASSWORD}, timeout=30)
        resp = s.get(NEWDAY_URL, params={"date": day.isoformat()}, timeout=60)
        resp.raise_for_status()
    m = _PAYLOAD.search(resp.text)
    if not m:
        if 'name="password"' in resp.text:
            raise RuntimeError("newday.ee login failed — check NEWDAY_USER / NEWDAY_PASSWORD")
        raise RuntimeError("newday.ee aFRR page has no price data (page format changed?)")
    payload = json.loads(m.group(1)).get("payload", {})
    points = []
    for key, direction in (("up", "UP"), ("down", "DOWN")):
        for ts, price in payload.get(key) or []:
            if price is not None:
                points.append((int(ts), direction, float(price)))
    return points


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
        db["afrr_cbmp_days"].upsert({"day": day.isoformat(), "fetched_at": now.isoformat(), "points": len(points)}, pk="day")
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
