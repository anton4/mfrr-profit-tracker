# fees.py — electricity import/export prices with seller and network fees (Estonia)
#
#   tariff      = margin + taastuv + aktsiis + tasakaal + varustus + (elektrilevi_day | elektrilevi_night)
#   import      = (spot + tariff) × (1 + VAT/100)
#   export      = spot − export_margin − export_tasakaal          (no VAT)
#
# Night rate: hour < 07:00 or >= 22:00, Saturdays, Sundays and Estonian public holidays.
# Fee values are cents/kWh excluding VAT; spot prices are €/kWh.
import json
import os
from datetime import date, datetime, timedelta
from functools import lru_cache

import pytz
from sqlite_utils import Database

DB_PATH = "data/mffr.db"
tz = pytz.timezone("Europe/Tallinn")

# Defaults (s/kWh excl. VAT, Oct 2026): Elering taastuvenergia tasu 0.84, elektriaktsiis
# 3.07 €/MWh from 1 May 2026, tasakaalustamisvõimsuse tasu 0.373, varustuskindluse tasu 0.758,
# Elektrilevi Võrk 2 day/night 7.53/4.35 incl. VAT. Seller margins depend on the contract.
FIELDS = {
    "margin":            ("Seller margin",                 0.0),
    "taastuv":           ("Renewable energy fee",          0.84),
    "aktsiis":           ("Electricity excise",            0.307),
    "tasakaal":          ("Balancing capacity fee",        0.373),
    "varustus":          ("Security of supply fee",        0.758),
    "elektrilevi_day":   ("Elektrilevi day",               6.07),
    "elektrilevi_night": ("Elektrilevi night",             3.51),
    "vat":               ("VAT %",                         24.0),
    "export_margin":     ("Export margin",                 0.0),
    "export_tasakaal":   ("Export balancing fee",          0.0),
}


def _default(key: str, fallback: float) -> float:
    env = os.getenv(f"FEE_{key.upper()}")
    if env not in (None, ""):
        return float(env)
    if key == "vat" and os.getenv("GRID_IMPORT_MULT"):
        return round((float(os.environ["GRID_IMPORT_MULT"]) - 1.0) * 100.0, 4)   # back-compat
    return fallback


DEFAULTS = {key: _default(key, value) for key, (_, value) in FIELDS.items()}


def get_fees(db: Database | None = None) -> dict:
    """Defaults overlaid with the values saved from the UI."""
    db = db or Database(DB_PATH)
    values = dict(DEFAULTS)
    if "settings" in db.table_names():
        try:
            saved = json.loads(db["settings"].get("fees")["value"])
            values.update({k: float(v) for k, v in saved.items() if k in FIELDS})
        except Exception:
            pass
    return values


def save_fees(values: dict, db: Database | None = None) -> dict:
    """Validate and store fee values (s/kWh, VAT in %). Raises ValueError on bad input."""
    clean = {}
    for key, value in values.items():
        if key not in FIELDS:
            raise ValueError(f"Unknown fee '{key}'")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"'{key}' must be a number")
        low, high = (0.0, 100.0) if key == "vat" else (-100.0, 100.0)
        if not low <= value <= high:
            raise ValueError(f"'{key}' must be between {low:g} and {high:g}")
        clean[key] = float(value)
    db = db or Database(DB_PATH)
    merged = {**get_fees(db), **clean}
    db["settings"].upsert({"key": "fees", "value": json.dumps(merged)}, pk="key")
    return merged


# ---- time of use ----

def _easter(year: int) -> date:
    """Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


@lru_cache(maxsize=32)
def estonian_holidays(year: int) -> frozenset:
    easter = _easter(year)
    fixed = [(1, 1), (2, 24), (5, 1), (6, 23), (6, 24), (8, 20), (12, 24), (12, 25), (12, 26)]
    return frozenset([date(year, m, d) for m, d in fixed]
                     + [easter - timedelta(days=2), easter, easter + timedelta(days=49)])


def is_night(slot: datetime) -> bool:
    """Night/weekend/holiday network rate for a slot (by its local Tallinn start time)."""
    local = slot.astimezone(tz)
    return (local.hour < 7 or local.hour >= 22 or local.weekday() >= 5
            or local.date() in estonian_holidays(local.year))


# ---- prices (€/kWh) ----

def import_price(spot: float, slot: datetime, fees: dict, with_fees: bool) -> float:
    vat = 1.0 + fees["vat"] / 100.0
    if not with_fees:
        return spot * vat
    network = fees["elektrilevi_night"] if is_night(slot) else fees["elektrilevi_day"]
    tariff_cents = (fees["margin"] + fees["taastuv"] + fees["aktsiis"] + fees["tasakaal"]
                    + fees["varustus"] + network)
    return (spot + tariff_cents / 100.0) * vat


def export_price(spot: float, fees: dict, with_fees: bool) -> float:
    if not with_fees:
        return spot
    return spot - (fees["export_margin"] + fees["export_tasakaal"]) / 100.0


def bill_effect(row: dict, with_fees: bool, fees: dict) -> float | None:
    """Electricity bill effect (€) of an activation row: metered import/export vs. what the
    baseline would have imported/exported over the same time. None if prices are missing."""
    spot = row.get("nordpool_price")
    if spot is None:
        return None
    slot = datetime.fromisoformat(row.get("price_timeslot") or row["timeslot"])
    p_import = import_price(spot, slot, fees, with_fees)
    p_export = export_price(spot, fees, with_fees)

    grid_kwh = row.get("grid_kwh") or 0.0
    grid_import = row.get("grid_import_kwh")
    grid_export = row.get("grid_export_kwh")
    if grid_import is None or grid_export is None:
        grid_import, grid_export = max(0.0, grid_kwh), max(0.0, -grid_kwh)

    baseline_import = row.get("baseline_import_kwh")
    baseline_export = row.get("baseline_export_kwh")
    if baseline_import is not None and baseline_export is not None:
        return ((grid_export - baseline_export) * p_export
                - (grid_import - baseline_import) * p_import)
    # Legacy rows recorded before baseline energy was tracked: all import (DOWN) / export (UP)
    if row.get("signal") == "DOWN":
        return -p_import * grid_import
    return p_export * grid_export
