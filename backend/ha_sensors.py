# ha_sensors.py — publish summary sensors to Home Assistant (POST /api/states)
#
# States set through the REST API have no unique_id and are gone after an HA restart until the
# next push, so everything is recomputed from the database and pushed every minute.
import os
from datetime import datetime

import pytz
import requests
from sqlite_utils import Database

import config
import fees
from ha import HA_URL, _HEADERS

PUBLISH_SENSORS = os.getenv("PUBLISH_SENSORS", "false").strip().lower() in ("1", "true", "yes", "on")
PREFIX = os.getenv("SENSOR_PREFIX", "mfrr")

tz = pytz.timezone("Europe/Tallinn")

_EUR = {"unit_of_measurement": "EUR", "device_class": "monetary"}
_KWH = {"unit_of_measurement": "kWh", "device_class": "energy"}
_PRICE = {"unit_of_measurement": "EUR/kWh"}


def _totals(rows: list[dict]) -> dict:
    priced = [r for r in rows if r.get("net_total") is not None]
    return {
        "net": round(sum(r["net_total"] for r in priced), 2),
        "net_with_fees": round(sum(r.get("net_total_fees") or 0.0 for r in priced), 2),
        "activation_income": round(sum(r.get("profit") or 0.0 for r in priced), 2),
        "kratt_fee": round(sum(r.get("kratt_fee") or 0.0 for r in priced), 2),
        "energy_kwh": round(sum(r.get("energy_kwh") or 0.0 for r in rows), 3),
        "slots": len(rows),
        "unpriced_slots": len(rows) - len(priced),
    }


def _latest_price(rows: list[dict], market: str, direction: str) -> dict | None:
    """Price of the newest priced activation for a market and direction (rows are newest first)."""
    for r in rows:
        if (r.get("market") or "MFRR") == market and r.get("signal") == direction and r.get("mffr_price") is not None:
            return r
    return None


def build_states(db: Database, now: datetime | None = None) -> dict[str, tuple]:
    """entity_id → (state, attributes) for every published sensor."""
    now = (now or datetime.now(tz)).astimezone(tz)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)

    fee_values = fees.get_fees(db)
    rows = [fees.add_fee_columns(r, fee_values)
            for r in db["slots"].rows_where("energy_kwh > 0", order_by="timeslot desc")]
    today = _totals([r for r in rows if r["timeslot"] >= day_start.isoformat()])
    month = _totals([r for r in rows if r["timeslot"] >= month_start.isoformat()])
    total = _totals(rows)

    states = {
        # Running totals: state_class "total" gives long-term statistics (statistics cards,
        # Energy dashboard). Not total_increasing, because a backfill can lower them.
        f"sensor.{PREFIX}_net_total": (total["net"], {
            **_EUR, "state_class": "total", "friendly_name": "mFRR net result (total)", **total}),
        f"sensor.{PREFIX}_energy_total": (total["energy_kwh"], {
            **_KWH, "state_class": "total", "friendly_name": "mFRR delivered energy (total)"}),
        f"sensor.{PREFIX}_net_today": (today["net"], {
            **_EUR, "friendly_name": "mFRR net result today", **today}),
        f"sensor.{PREFIX}_net_month": (month["net"], {
            **_EUR, "friendly_name": "mFRR net result this month", **month}),
        f"sensor.{PREFIX}_energy_today": (today["energy_kwh"], {
            **_KWH, "friendly_name": "mFRR delivered energy today"}),
    }

    last = rows[0] if rows else None
    states[f"sensor.{PREFIX}_last_activation"] = (
        datetime.fromisoformat(last.get("start") or last["timeslot"]).isoformat() if last else "unknown",
        {"device_class": "timestamp", "friendly_name": "mFRR last activation",
         **({"timeslot": last["timeslot"], "market": last.get("market"), "direction": last.get("signal"),
             "energy_kwh": last.get("energy_kwh"), "net": last.get("net_total"),
             "net_with_fees": last.get("net_total_fees")} if last else {})})

    # Prices of the latest priced activation per market and direction. mFRR prices are published
    # about an hour after the slot and only fetched for slots with an activation.
    for market in ("MFRR", "AFRR"):
        for direction in ("UP", "DOWN"):
            r = _latest_price(rows, market, direction)
            entity = f"sensor.{market.lower()}_price_{direction.lower()}"
            name = f"{'mFRR' if market == 'MFRR' else 'aFRR'} price {direction}"
            states[entity] = (
                round(r["mffr_price"] / 1000.0, 5) if r else "unknown",
                {**_PRICE, "friendly_name": name,
                 **({"eur_mwh": r["mffr_price"], "timeslot": r.get("price_timeslot") or r["timeslot"],
                     "source": r.get("price_source")} if r else {})})
    return states


def publish_sensors():
    if not PUBLISH_SENSORS:
        return
    db = Database(config.DB_PATH)
    try:
        db.conn.execute("PRAGMA busy_timeout=5000;")
        if "slots" not in db.table_names():
            return
        states = build_states(db)
    finally:
        db.conn.close()

    for entity_id, (state, attributes) in states.items():
        try:
            resp = requests.post(f"{HA_URL}/api/states/{entity_id}", headers=_HEADERS,
                                 json={"state": state, "attributes": attributes}, timeout=5)
            if not resp.ok:
                print(f"❌ Failed to publish {entity_id}: {resp.status_code} {resp.text[:200]}")
                return
        except Exception as e:
            print(f"❌ Error publishing {entity_id}: {e}")
            return
