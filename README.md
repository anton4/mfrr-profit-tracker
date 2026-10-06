**MFFR Profit Tracker**
=======================

Track Manual Frequency Restoration Reserve (mFRR) activations dispatched by **Kratt**, along with grid energy, prices, and profits, in near real-time using Home Assistant. It runs as a single container: a FastAPI backend that also serves the React frontend.

* * * * *

**Setup**
---------

### **1\. Clone the Repository**

```
git clone https://github.com/martinarva/mffr-profit-tracker.git
cd mffr-profit-tracker
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
SENSOR_GRID=sensor.ss_grid_power
SENSOR_NORDPOOL=sensor.nordpool_kwh_ee_eur_3_10_0
KRATT_SHARE=0.20
```

#### **Home Assistant Sensor Notes:**

-   SENSOR_SOURCE: Qilowatt source sensor. An mFRR command is active while its state is **`kratt`** (case-insensitive).

-   SENSOR_MODE: Qilowatt mode/command sensor that gives the direction. **`BUY`** = DOWN, **`SELL`** / **`FRRUP`** = UP.

-   SENSOR_GRID: Grid power sensor in **watts**. Positive = importing from grid, negative = exporting to grid. Kratt measures at the grid connection point, so this is the sensor used for both the baseline and the delivered mFRR energy.

-   SENSOR_NORDPOOL: Nordpool integration sensor (no VAT/tariffs), price in €/kWh.

-   KRATT_SHARE: Share of activation revenue kept by Kratt (default 0.20 = 20%).

* * * * *

**Baseline & mFRR energy**
--------------------------

`baseline.py` runs every 10 seconds and averages **grid power** over each 15-minute slot that had no mFRR command. That average is the baseline.

-   The baseline is locked for the whole run of back-to-back commands. A new baseline is only taken after a full idle 15-minute slot.

-   Only deviation in the commanded direction counts:

```
DOWN: mFRR power (W) = max(0, grid_power - baseline)   # extra import
UP:   mFRR power (W) = max(0, baseline - grid_power)   # extra export / less import
```

* * * * *

**mFRR prices**
---------------

`mffr_price_updater.py` fetches 15-minute mFRR balancing energy prices (€/MWh, Estonia Upward / Downward) from the [Baltic Transparency Dashboard](https://baltic.transparency-dashboard.eu/) published by Elering, AST and Litgrid. UP slots get the upward price and DOWN slots get the downward price. Prices are published about 1 hour after the slot. Unpriced slots from the last 7 days are retried every minute.

Set `MFFR_PRICE_AREA` (`Estonia` / `Latvia` / `Lithuania`) to use another bidding zone.

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
docker logs -f mffr-tracker
```

### **Local development**

```
cd backend && uvicorn api:app --reload        # API on :8000
cd frontend && npm install && npm run dev     # UI on :5173, proxies /api to :8000
```

* * * * *

**Contributions**
-----------------

Pull requests are welcome! This is a simple tool meant to help track mFRR-based home battery strategies.

* * * * *

**License:** MIT
