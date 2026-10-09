# fees.py — electricity import/export prices with seller and network fees (Estonia)
#
#   tariff      = margin + taastuv + aktsiis + tasakaal + varustus + network rate (day | night | peak)
#   import      = (spot + tariff) × (1 + VAT/100)
#   export      = spot − export_margin − export_tasakaal          (no VAT)
#
# Night rate: hour < 07:00 or >= 22:00, Saturdays, Sundays and Estonian public holidays.
# Peak rates (Elektrilevi Võrk 5, or a custom package with peak prices), November–March only:
#   day peak: working days 09–12 and 16–20; weekend peak: weekends and holidays 16–20.
# Fee values are cents/kWh excluding VAT; spot prices are €/kWh.
import os
from datetime import date, datetime, timedelta
from functools import lru_cache

import pytz

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
    "elektrilevi_day":   ("Network day",                   6.07),
    "elektrilevi_night": ("Network night / weekend",       3.51),
    "elektrilevi_day_peak":     ("Network day peak",       0.0),
    "elektrilevi_holiday_peak": ("Network weekend peak",   0.0),
    "vat":               ("VAT",                           24.0),
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


NETWORK_KEYS = ("elektrilevi_day", "elektrilevi_night", "elektrilevi_day_peak", "elektrilevi_holiday_peak")

# Elektrilevi network packages, low voltage up to 63 A: price list valid from 1 June 2026,
# "elektri edastamine" in cents/kWh excl. VAT. Monthly fees are fixed costs and don't depend on
# mFRR, so they're left out. "custom": another network operator or own prices.
PACKAGES = {
    "vork1": {"label": "Elektrilevi Võrk 1", "note": "One price around the clock",
              "rates": {"elektrilevi_day": 7.72, "elektrilevi_night": 7.72,
                        "elektrilevi_day_peak": 0.0, "elektrilevi_holiday_peak": 0.0}},
    "vork2": {"label": "Elektrilevi Võrk 2", "note": "Day / night",
              "rates": {"elektrilevi_day": 6.07, "elektrilevi_night": 3.51,
                        "elektrilevi_day_peak": 0.0, "elektrilevi_holiday_peak": 0.0}},
    "vork4": {"label": "Elektrilevi Võrk 4", "note": "Day / night, higher monthly fee",
              "rates": {"elektrilevi_day": 3.69, "elektrilevi_night": 2.10,
                        "elektrilevi_day_peak": 0.0, "elektrilevi_holiday_peak": 0.0}},
    "vork5": {"label": "Elektrilevi Võrk 5", "note": "Day / night + winter peak hours",
              "rates": {"elektrilevi_day": 5.29, "elektrilevi_night": 3.03,
                        "elektrilevi_day_peak": 8.18, "elektrilevi_holiday_peak": 4.74}},
    "custom": {"label": "Custom", "note": "Other network operator or own prices", "rates": None},
}
DEFAULT_PACKAGE = os.getenv("FEE_NETWORK_PACKAGE", "vork2")
if DEFAULT_PACKAGE not in PACKAGES:
    DEFAULT_PACKAGE = "vork2"

DEFAULTS = {key: _default(key, value) for key, (_, value) in FIELDS.items()}
if PACKAGES[DEFAULT_PACKAGE]["rates"]:
    DEFAULTS.update(PACKAGES[DEFAULT_PACKAGE]["rates"])
DEFAULTS["network_package"] = DEFAULT_PACKAGE


def get_fees() -> dict:
    """Fee settings from the add-on options (FEE_* in a standalone .env)."""
    return dict(DEFAULTS)


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


def _rest_day(local: datetime) -> bool:
    return local.weekday() >= 5 or local.date() in estonian_holidays(local.year)


def is_night(slot: datetime) -> bool:
    """Night/weekend/holiday network rate for a slot (by its local Tallinn start time)."""
    local = slot.astimezone(tz)
    return local.hour < 7 or local.hour >= 22 or _rest_day(local)


def network_period(slot: datetime, fees: dict) -> str:
    """'day', 'night', 'day_peak' or 'holiday_peak' for a slot's local start time.

    Peak periods only exist when the package has peak prices (Võrk 5, or custom with them).
    """
    local = slot.astimezone(tz)
    winter = local.month in (11, 12, 1, 2, 3)
    if winter and _rest_day(local):
        if fees.get("elektrilevi_holiday_peak") and 16 <= local.hour < 20:
            return "holiday_peak"
    elif winter and fees.get("elektrilevi_day_peak") and (9 <= local.hour < 12 or 16 <= local.hour < 20):
        return "day_peak"
    return "night" if is_night(local) else "day"


NETWORK_RATE_KEY = {"day": "elektrilevi_day", "night": "elektrilevi_night",
                    "day_peak": "elektrilevi_day_peak", "holiday_peak": "elektrilevi_holiday_peak"}


# ---- prices (€/kWh) ----

def import_price(spot: float, slot: datetime, fees: dict, with_fees: bool) -> float:
    vat = 1.0 + fees["vat"] / 100.0
    if not with_fees:
        return spot * vat
    network = fees[NETWORK_RATE_KEY[network_period(slot, fees)]]
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


def add_fee_columns(row: dict, fees: dict) -> dict:
    """Add the 'with seller and network fees' variant to a slot row, computed with the current
    fee settings so edits apply instantly (the stored grid_cost / net_total are spot + VAT only)."""
    slot = row.get("price_timeslot") or row.get("timeslot")
    row["tariff_period"] = network_period(datetime.fromisoformat(slot), fees) if slot else None
    bill = bill_effect(row, with_fees=True, fees=fees) if row.get("profit") is not None else None
    if bill is None:
        row["grid_cost_fees"] = row["net_total_fees"] = row["price_per_kwh_fees"] = row["fees_eur"] = None
        return row
    # What seller and network fees add (−) or save (+) on top of the spot + VAT bill effect
    row["fees_eur"] = round(bill + (row.get("grid_cost") or 0.0), 5)
    net = row["profit"] + bill
    energy = row.get("energy_kwh") or 0.0
    row["grid_cost_fees"] = round(-bill, 5)
    row["net_total_fees"] = round(net, 5)
    row["price_per_kwh_fees"] = round(net / energy, 5) if energy > 0 else None
    return row
