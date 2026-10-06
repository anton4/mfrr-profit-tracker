from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import os
import pytz
from sqlite_utils import Database

import fees

DB_PATH = "data/mffr.db"
tz = pytz.timezone("Europe/Tallinn")

# ---- Tunables (can be overridden via env) ----
# Kratt keeps 20% → you receive 80% of activation revenue
KRATT_SHARE = float(os.getenv("KRATT_SHARE", "0.20"))   # 0.20 = 20%
# Minimum energy to consider (filter noise)
MIN_ENERGY_KWH = float(os.getenv("MIN_ENERGY_KWH", "0.00001"))

scheduler = BackgroundScheduler()


def run_profit_calculation():
    db = Database(DB_PATH)
    now = datetime.now(tz)
    updated = False
    fee_values = fees.get_fees(db)

    # Only (re)compute finished slots
    for row in db["slots"].rows_where("profit IS NULL OR net_total IS NULL"):
        try:
            slot_end = datetime.fromisoformat(row["slot_end"])
            if slot_end > now:
                continue  # slot still running
        except Exception:
            continue

        direction   = row.get("signal")              # "UP" or "DOWN"
        energy_kwh  = row.get("energy_kwh")          # >= 0, grid deviation in commanded direction
        grid_kwh    = row.get("grid_kwh")            # net: +import, -export
        mffr_price  = row.get("mffr_price")          # €/MWh from your updater
        nps_price   = row.get("nordpool_price")      # €/kWh (Nordpool)

        if direction is None or energy_kwh is None:
            continue
        if energy_kwh < MIN_ENERGY_KWH:
            # Ignore microscopic slots
            continue
        if mffr_price is None or nps_price is None or grid_kwh is None:
            # Missing prices or grid energy → skip for now
            continue

        # Convert mFRR €/MWh → €/kWh
        mffr_eur_per_kwh = (mffr_price / 1000.0)

        # Your share of activation revenue after Kratt
        your_share = (1.0 - KRATT_SHARE)

        if direction == "DOWN":
            # Commanded DOWN: you increase grid import (or reduce export).
            # Activation revenue is (nps - mffr) * energy: compensation for the energy you absorb.
            activation_income = (nps_price - mffr_eur_per_kwh) * energy_kwh * your_share
        elif direction == "UP":
            # Commanded UP: you increase grid export (or reduce import).
            # Activation revenue is (mffr - nps) * energy: on top of the nps you get for that energy.
            activation_income = (mffr_eur_per_kwh - nps_price) * energy_kwh * your_share
        else:
            # Unknown direction
            continue
        kratt_fee = activation_income * (KRATT_SHARE / your_share) if your_share > 0 else 0.0

        # Electricity bill effect of the activation only (vs. the baseline), spot + VAT on import.
        # The variant with seller and network fees is computed per request in api.py.
        bill_effect = fees.bill_effect(row, with_fees=False, fees=fee_values)

        net_total     = activation_income + bill_effect
        price_per_kwh = (net_total / energy_kwh) if energy_kwh > 0 else None

        update = {
            "profit":        round(activation_income, 5),   # activation share after Kratt's fee
            "kratt_fee":     round(kratt_fee, 5),
            "grid_cost":     round(-bill_effect, 5),        # negative cost = income
            "net_total":     round(net_total, 5),
            "price_per_kwh": round(price_per_kwh, 5) if price_per_kwh is not None else None,
        }

        if update:
            db["slots"].update(row["id"], update, alter=True)
            updated = True
            print(f"📊 Updated slot {row['id']} → {update}")

    if updated:
        print("✅ Profit + financial breakdown updated.")


# Run every minute; scheduler is started by api.py
scheduler.add_job(run_profit_calculation, "interval", minutes=1)