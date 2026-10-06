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
SENSOR_GRID_IMPORT=sensor.shellyem3_485519dbeee9_channel_a_energy,sensor.shellyem3_485519dbeee9_channel_b_energy,sensor.shellyem3_485519dbeee9_channel_c_energy
SENSOR_GRID_EXPORT=sensor.shellyem3_485519dbeee9_channel_a_energy_returned,sensor.shellyem3_485519dbeee9_channel_b_energy_returned,sensor.shellyem3_485519dbeee9_channel_c_energy_returned
SENSOR_NORDPOOL=sensor.nordpool_kwh_ee_eur_3_10_0
KRATT_SHARE=0.20
```

#### **Home Assistant Sensor Notes:**

-   SENSOR_SOURCE: Qilowatt source sensor. An mFRR command is active while its state is **`kratt`** (case-insensitive).

-   SENSOR_MODE: Qilowatt mode/command sensor that gives the direction. **`BUY`** = DOWN, **`SELL`** / **`FRRUP`** = UP.

-   SENSOR_GRID_IMPORT / SENSOR_GRID_EXPORT: Cumulative grid **energy** counters (Wh, kWh or MWh, read from `unit_of_measurement`), comma-separated. These are typically one per phase, e.g. Shelly 3EM `channel_a/b/c_energy` for import and `channel_a/b/c_energy_returned` for export. Kratt measures at the grid connection point, so these counters are used for the baseline, the delivered mFRR energy and the grid import/export.

-   SENSOR_NORDPOOL: Nordpool integration sensor (no VAT/tariffs), price in €/kWh.

-   KRATT_SHARE: Share of activation revenue kept by Kratt (default 0.20 = 20%).

* * * * *

**Baseline & mFRR energy**
--------------------------

Every 10 seconds the tracker reads the energy counters and takes the change since the previous read: **net grid energy = Σ import − Σ export** across all phases. Netting the phases at each read mimics a phase-summing utility meter, so one phase importing while another exports is not counted as both.

`baseline.py` averages the net grid power, measured from the counters, over each 15-minute slot that had no mFRR command. That average is the baseline. A slot only counts if the counters covered at least 12 minutes of it.

-   The baseline is locked for the whole run of back-to-back commands. A new baseline is only taken after a full idle 15-minute slot.

-   Delivered mFRR energy is the slot's metered net grid energy vs. the baseline over the same metered time. Only deviation in the commanded direction counts:

```
baseline_kWh = baseline_W × metered_time
DOWN: mFRR energy = max(0, net_grid_kWh − baseline_kWh)   # extra import
UP:   mFRR energy = max(0, baseline_kWh − net_grid_kWh)   # extra export / less import
```

-   Grid import and export are also stored separately per slot (`grid_import_kwh`, `grid_export_kwh`). Profit uses them for the DOWN import cost and the UP export income.

* * * * *

**mFRR prices**
---------------

`mffr_price_updater.py` fetches 15-minute mFRR balancing energy prices (€/MWh, Estonia Upward / Downward) from the [Baltic Transparency Dashboard](https://baltic.transparency-dashboard.eu/) published by Elering, AST and Litgrid. UP slots get the upward price and DOWN slots get the downward price. Prices are published about 1 hour after the slot. Unpriced slots from the last 7 days are retried every minute.

Set `MFRR_PRICE_AREA` (`Estonia` / `Latvia` / `Lithuania`) to use another bidding zone.

* * * * *

**Code layout**
---------------

-   backend/api.py: FastAPI app. Serves `/api/mffr` and the built UI, and starts all schedulers.

-   backend/ha.py: Home Assistant access and Kratt signal detection.

-   backend/main.py: Polls Home Assistant every 10 seconds and writes mFRR slot data to the database.

-   backend/baseline.py: Tracks average grid power during idle slots.

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
