# The fee breakdown must add up to what fees change in the bill effect
from datetime import datetime

import pytz

import fees

tz = pytz.timezone("Europe/Tallinn")


def _row(**kw):
    row = {"timeslot": tz.localize(datetime(2026, 11, 20, 18, 0)).isoformat(), "signal": "UP",
           "nordpool_price": 0.12, "profit": 1.0, "grid_cost": 0.0, "energy_kwh": 5.0}
    row.update(kw)
    row["grid_cost"] = -fees.bill_effect(row, with_fees=False, fees=FEES)
    return row


FEES = {**fees.DEFAULTS, **fees.PACKAGES["vork5"]["rates"], "network_package": "vork5",
        "margin": 0.5, "export_margin": 0.3, "export_tasakaal": 0.1}


def _check(row):
    fees.add_fee_columns(row, FEES)
    parts = sum(row[c] for c in fees.FEE_PARTS)
    assert abs(parts - row["fees_eur"]) < 1e-4
    return row


def test_up_by_cutting_import_saves_fees():
    # Charging from the grid at 18.8 kW, Kratt cut it to 10.8 kW: 2 kWh less import in the slot
    row = _check(_row(grid_import_kwh=2.7, grid_export_kwh=0.0, baseline_import_kwh=4.7, baseline_export_kwh=0.0))
    assert row["tariff_period"] == "day_peak" and row["network_package"] == "Elektrilevi Võrk 5"
    assert abs(row["fees_network_eur"] - 2 * 8.18 / 100) < 1e-6
    assert row["fees_eur"] > 0 and row["fees_export_eur"] == 0


def test_down_with_extra_import_and_less_export():
    row = _check(_row(signal="DOWN", grid_import_kwh=3.0, grid_export_kwh=0.5,
                      baseline_import_kwh=0.0, baseline_export_kwh=2.0))
    assert row["fees_network_eur"] < 0 and row["fees_vat_eur"] < 0
    assert row["fees_export_eur"] > 0    # 1.5 kWh less export: export fees not paid


def test_legacy_rows_without_baseline_energy():
    _check(_row(signal="DOWN", grid_kwh=4.0))
    _check(_row(signal="UP", grid_kwh=-4.0))
