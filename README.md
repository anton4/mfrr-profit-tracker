**mFRR Profit Tracker**
=======================

Track Manual Frequency Restoration Reserve (mFRR) activations dispatched by **Kratt**, along with grid energy, prices, and profits, in near real-time using Home Assistant. It runs as a single container: a FastAPI backend that also serves the React frontend.

> Originally created by [martinarva](https://github.com/martinarva) as [MFFR-Profit-Tracker](https://github.com/martinarva/MFFR-Profit-Tracker) for Fusebox. This fork adapts it to Kratt.

* * * * *

**Setup**
---------

### **1\. Clone the Repository**

```
git clone https://github.com/anton4/mfrr-profit-tracker.git
cd mfrr-profit-tracker
```

### **2\. Environment Configuration**

Copy and edit the environment file:

```
cp .env.example .env
```

#### **.env example:**

```
TZ=Europe/Tallinn
HA_URL=http://your-ha.local:8123
HA_TOKEN=your_long_token_here
SENSOR_SOURCE=sensor.qw_source
SENSOR_MODE=sensor.qw_mode
SENSOR_POWERLIMIT=sensor.qw_powerlimit
SENSOR_GRID_POWER=sensor.shellyem3_485519dbeee9_channel_a_power,sensor.shellyem3_485519dbeee9_channel_b_power,sensor.shellyem3_485519dbeee9_channel_c_power
SENSOR_NORDPOOL=sensor.nordpool_kwh_ee_eur_3_10_0
KRATT_SHARE=0.20
```

#### **Home Assistant Sensor Notes:**

-   SENSOR_SOURCE: Qilowatt source sensor. An mFRR command is active while its state is **`Kratt`** (case-insensitive).

-   SENSOR_MODE: Qilowatt mode sensor that gives the direction. **`frrdown`** = DOWN, **`frrup`** = UP.

-   SENSOR_POWERLIMIT (optional): Qilowatt power limit sensor, the power Kratt requested (W or kW). Used to record requested energy and delivery % per slot.

-   SENSOR_GRID_POWER: Grid **power** sensors, one per phase, comma-separated (W or kW, read from `unit_of_measurement`). Positive = importing, negative = exporting, e.g. Shelly 3EM `channel_a/b/c_power`. They should update every few seconds. Kratt measures at the grid connection point, so these sensors are used for the baseline, the delivered mFRR energy and the grid import/export. Cumulative energy counters aren't used, because they typically update only about once a minute.

-   SENSOR_NORDPOOL: Nordpool integration sensor (no VAT/tariffs), price in €/kWh.

-   KRATT_SHARE: Share of activation revenue kept by Kratt (default 0.20 = 20%).

* * * * *

**Baseline & mFRR energy**
--------------------------

Every 10 seconds the tracker reads all phase powers and sums them into **net grid power** (+import / −export). It integrates that power over the actual time since the previous read (trapezoidal). Summing signed phase powers mimics a phase-summing utility meter, so one phase importing while another exports is not counted as both. Missing readings, e.g. when Home Assistant is unreachable or a sensor is unavailable, are handled like this:

-   Gaps up to 60 s are interpolated between the readings on either side.

-   Longer gaps are filled only if the power is the same on both sides (within 100 W or 3%, whichever is larger). The power is then assumed constant for the whole gap. A filled gap is cut off at the start of the current 15-minute slot, so energy from earlier slots isn't added to it.

-   Longer gaps where the power changed are skipped, because it's unknown when the change happened.

**Baseline.** Qilowatt describes the KratTrade (Kratt) baseline as *"your system's current state — how much energy you are currently exporting or importing from the grid"*, and says that you earn from the change relative to it, measured on the grid side ([Qilowatt flexibility market](https://qilowatt.eu/en/flexibility-market/)). The tracker therefore:

-   Takes the baseline as the average net grid power over the **60 s of idle readings just before the signal** was detected. Readings after the signal are excluded, so the battery's own response doesn't leak into the baseline.

-   Keeps the baseline **locked for the whole run**, i.e. while `qw_source` stays `Kratt`, including direction or power-limit changes. The next signal after the run ends takes a new snapshot.

-   Falls back to 0 W, and logs it, when there's no idle data before the signal, e.g. right after startup.

**Delivered mFRR energy** is the slot's metered net grid energy compared with the baseline energy over the same metered time. The baseline energy is added up reading by reading, so two runs in one slot each keep their own baseline. Only deviation in the commanded direction counts:

```
DOWN: mFRR energy = max(0, net_grid_kWh − baseline_kWh)   # extra import / less export
UP:   mFRR energy = max(0, baseline_kWh − net_grid_kWh)   # extra export / less import
```

-   Requested energy is `qw_powerlimit` integrated over the same metered time (`requested_kwh`). **Delivery %** = delivered mFRR energy / requested energy, a rough measure of how well the battery followed the command.

-   Grid import and export are stored separately per slot (`grid_import_kwh`, `grid_export_kwh`), along with what the baseline would have imported and exported over the same time (`baseline_import_kwh`, `baseline_export_kwh`).

**Profit** (`profit_calc.py`):

```
activation = (mFRR − NPS) × mFRR energy × (1 − KRATT_SHARE)    # UP
activation = (NPS − mFRR) × mFRR energy × (1 − KRATT_SHARE)    # DOWN
bill effect = (export − baseline export) × NPS − (import − baseline import) × NPS × GRID_IMPORT_MULT
net = activation + bill effect
```

Only the change against the baseline counts toward the bill effect. Normal household consumption and PV export that would have happened anyway aren't attributed to mFRR. `GRID_IMPORT_MULT` (default 1.24) adds VAT on imported energy. Network fees are not included.

The activation formulas follow community knowledge of Fusebox settlement. Kratt hasn't published its exact settlement formula, so treat the numbers as estimates.

The value of the energy stored in or taken from the battery isn't counted. DOWN therefore looks worse than it is, because you get stored energy for later use. UP looks better than it is, because you spent stored energy.

* * * * *

**mFRR prices**
---------------

`mffr_price_updater.py` fetches 15-minute mFRR balancing energy prices (€/MWh, Estonia Upward / Downward) from the [Baltic Transparency Dashboard](https://baltic.transparency-dashboard.eu/) published by Elering, AST and Litgrid. UP slots get the upward price and DOWN slots get the downward price. Prices are published about 1 hour after the slot. Unpriced slots from the last 7 days are retried every minute.

Set `MFRR_PRICE_AREA` (`Estonia` / `Latvia` / `Lithuania`) to use another bidding zone.

The sync runs every minute and always checks the last 3 hours, so the latest published price is known even when no slot is waiting. The UI shows the last sync (and any error), the next sync, and the newest slot with published prices. The same information is available at `/api/price-sync`.

* * * * *

**Backfill from Home Assistant**
--------------------------------

Rebuilds the slot rows for a past period from Home Assistant's recorded history. Use it for periods when the tracker wasn't running, or to recompute rows recorded by older versions.

-   The tracker reads the history of `SENSOR_SOURCE`, `SENSOR_MODE`, `SENSOR_POWERLIMIT` and the `SENSOR_GRID_POWER` phases through the HA REST history API, with the same `HA_URL` / `HA_TOKEN`. It replays that history every 10 s through **the same logic as the live tracker**: signal detection, power integration and gap handling, the signal-time baseline, and per-direction rows. Backfilled rows are therefore calculated exactly like live ones.

-   HA stores only state changes, so between changes the last value is assumed to hold. The replay starts 30 minutes before the range so the meter and the pre-signal baseline are primed.

-   **Existing rows in the range are deleted and recomputed.** The range is rounded to 15-minute slots and stops at the start of the current slot, so it never touches the slot the live tracker is writing.

-   mFRR prices come from the Baltic Transparency Dashboard for the whole range. Nord Pool prices come from Elering's public API (`dashboard.elering.ee/api/nps/price`, Estonia). Profit is then recalculated.

-   HA keeps history for the recorder's `purge_keep_days`, 10 days by default. Older ranges return a "no history" error.

How to run it:

-   **UI:** the "Backfill from Home Assistant" panel. Pick from/to and click Backfill. It shows progress and reloads the table when done.

-   **API:** `POST /api/backfill` with `{"from": "2026-09-28T00:00", "to": "2026-10-05T00:00"}` (local time). `GET /api/backfill` returns the status. Only one backfill runs at a time.

-   **CLI:** `docker exec mfrr-tracker python backfill.py --from 2026-09-28 --to 2026-10-05`

* * * * *

**Code layout**
---------------

-   backend/api.py: FastAPI app. Serves `/api/mffr`, `/api/price-sync`, `/api/backfill` and the built UI, and starts all schedulers.

-   backend/backfill.py / backend/history.py: Backfill from the HA history API.

-   backend/ha.py: Home Assistant access and Kratt signal detection.

-   backend/main.py: Polls Home Assistant every 10 seconds and writes mFRR slot data to the database.

-   backend/baseline.py: Takes and locks the baseline (grid state just before a Kratt signal).

-   backend/mffr_price_updater.py: Fills in missing mFRR prices.

-   backend/profit_calc.py: Calculates profit when all required fields are present.

-   backend/data/mffr.db: SQLite database storing all 15-min mFRR records.

-   frontend/src/App.jsx: React UI.

* * * * *

**Installation**
----------------

```
docker compose up --build -d
```

Open the UI at `http://your-ip:8099/`

Logs:

```
docker logs -f mfrr-tracker
```

### **Local development**

```
cd backend && uvicorn api:app --reload        # API on :8000
cd frontend && npm install && npm run dev     # UI on :5173, proxies /api to :8000
```

* * * * *

**Credits**
-----------

Original project and idea by [martinarva](https://github.com/martinarva): [martinarva/MFFR-Profit-Tracker](https://github.com/martinarva/MFFR-Profit-Tracker). This fork switches the tracker to Kratt with grid-side metering, takes mFRR prices from the Baltic Transparency Dashboard, and runs as a single container.

* * * * *

**Contributions**
-----------------

Pull requests are welcome! This is a simple tool meant to help track mFRR-based home battery strategies.

* * * * *

**License:** MIT
