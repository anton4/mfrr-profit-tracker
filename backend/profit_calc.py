from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
import os
import pytz
from sqlite_utils import Database

DB_PATH = "data/mffr.db"
tz = pytz.timezone("Europe/Tallinn")

# ---- Tunables (can be overridden via env) ----
# Kratt keeps 20% → you receive 80% of activation revenue
KRATT_SHARE = float(os.getenv("KRATT_SHARE", "0.20"))   # 0.20 = 20%
# VAT or multiplier applied to grid import cost (Nordpool energy only part)
GRID_IMPORT_MULT = float(os.getenv("GRID_IMPORT_MULT", "1.24"))
# Minimum energy to consider (filter noise)
MIN_ENERGY_KWH = float(os.getenv("MIN_ENERGY_KWH", "0.00001"))

scheduler = BackgroundScheduler()


def run_profit_calculation():
    db = Database(DB_PATH)
    now = datetime.now(tz)
    updated = False

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
        # Import/export metered separately (older rows only have the net)
        grid_import = row.get("grid_import_kwh")
        grid_export = row.get("grid_export_kwh")
        if grid_import is None or grid_export is None:
            grid_import = max(0.0, grid_kwh or 0.0)
            grid_export = max(0.0, -(grid_kwh or 0.0))
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

        update = {}

        if direction == "DOWN":
            # Commanded DOWN: you increase grid import.
            # Activation revenue is (nps - mffr) * energy (you absorb, so compare against nps).
            # Grid cost is applied on imported grid energy with multiplier.
            activation_income = (nps_price - mffr_eur_per_kwh) * energy_kwh * your_share
            kratt_fee         = activation_income * (KRATT_SHARE / your_share) if your_share > 0 else 0.0

            grid_import_kwh   = grid_import
            grid_cost         = nps_price * GRID_IMPORT_MULT * grid_import_kwh

            net_total         = activation_income - grid_cost
            price_per_kwh     = (net_total / grid_import_kwh) if grid_import_kwh > 0 else None

            update.update({
                "profit":       round(activation_income, 5),    # legacy "profit" = activation share
                "kratt_fee":    round(kratt_fee, 5),
                "grid_cost":    round(grid_cost, 5),
                "net_total":    round(net_total, 5),
                "price_per_kwh": round(price_per_kwh, 5) if price_per_kwh is not None else None,
            })

        elif direction == "UP":
            # Commanded UP: you increase grid export.
            # Activation revenue is (mffr - nps) * energy (you deliver against nps).
            activation_income = (mffr_eur_per_kwh - nps_price) * energy_kwh * your_share
            kratt_fee         = activation_income * (KRATT_SHARE / your_share) if your_share > 0 else 0.0

            # Export income component: nps * exported energy
            grid_export_kwh   = grid_export
            export_income     = nps_price * grid_export_kwh

            net_total         = activation_income + export_income
            price_per_kwh     = (net_total / energy_kwh) if energy_kwh > 0 else None

            # Keep legacy "grid_cost" column but put signed grid value there (was in your code)
            update.update({
                "profit":        round(activation_income, 5),
                "kratt_fee":     round(kratt_fee, 5),
                "grid_cost":     round(-export_income, 5),  # legacy name kept; negative cost = income
                "net_total":     round(net_total, 5),
                "price_per_kwh": round(price_per_kwh, 5) if price_per_kwh is not None else None,
            })

        else:
            # Unknown direction
            continue

        if update:
            db["slots"].update(row["timeslot"], update, alter=True)
            updated = True
            print(f"📊 Updated slot {row['timeslot']} → {update}")

    if updated:
        print("✅ Profit + financial breakdown updated.")


# Run every minute; scheduler is started by api.py
scheduler.add_job(run_profit_calculation, "interval", minutes=1)