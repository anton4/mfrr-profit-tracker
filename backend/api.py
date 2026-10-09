# api.py
import os
from contextlib import asynccontextmanager
from typing import Optional
from datetime import datetime

import pytz
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlite_utils import Database

import main
import profit_calc
import mffr_price_updater
import backfill
import qw_report
import fees
import config
import entsoe_cbmp
import ha
import sensors

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("✅ Starting all schedulers from FastAPI")
    ha.log_sensor_check()
    print("💶 aFRR market price (CBMP): "
          + ("ENTSO-E token set" if entsoe_cbmp.configured() else "ENTSO-E token not set, not shown"))
    main.write_current_timeslot()
    profit_calc.run_profit_calculation()
    mffr_price_updater.fetch_and_update_mffr_prices()
    for scheduler in (main.scheduler, profit_calc.scheduler, mffr_price_updater.scheduler):
        if not scheduler.running:
            scheduler.start()
    yield
    for scheduler in (main.scheduler, profit_calc.scheduler, mffr_price_updater.scheduler):
        if scheduler.running:
            scheduler.shutdown(wait=False)

app = FastAPI(lifespan=lifespan)
DB_FILE = config.DB_PATH
STATIC_DIR = "static"   # built frontend (copied in by the Dockerfile)

LOCAL_TZ = pytz.timezone(os.getenv("TZ", "Europe/Tallinn"))
VERSION = os.getenv("TRACKER_VERSION", "dev")
STARTED_AT = datetime.now(LOCAL_TZ).isoformat()   # changes on every restart

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _normalize_to_local_iso(ts: Optional[str]) -> Optional[str]:
    if not ts:
        return None
    try:
        s = ts.strip().replace(" ", "T").replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = LOCAL_TZ.localize(dt)
    return dt.astimezone(LOCAL_TZ).isoformat()

@app.get("/api/mffr")
def get_mffr_data(
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts:   Optional[str] = Query(None, alias="to"),
    limit:   int = Query(1000, ge=1, le=50000),
):
    # ✅ fresh handle per request/thread
    _db = Database(DB_FILE)
    try:
        _db.conn.execute("PRAGMA busy_timeout=5000;")
    except Exception:
        pass

    if "slots" not in _db.table_names():
        return {}

    nf = _normalize_to_local_iso(from_ts)
    nt = _normalize_to_local_iso(to_ts)

    where = []
    params = []
    if nf:
        where.append("timeslot >= ?")
        params.append(nf)
    if nt:
        where.append("timeslot <= ?")
        params.append(nt)
    where_clause = " AND ".join(where) if where else "1=1"

    try:
        rows = list(
            _db["slots"].rows_where(
                where_clause,
                where_args=params,          # ← correct keyword
                order_by="timeslot desc",
                limit=None if (nf or nt) else limit,
            )
        )
    except Exception as e:
        print(f"DB query failed: where='{where_clause}' args={params} err={e}")
        raise

    # Variant with seller and network fees, computed with the current fee settings
    fee_values = fees.get_fees(_db)
    for row in rows:
        fees.add_fee_columns(row, fee_values)

    # One row per slot, market and direction, keyed by id
    return {row["id"]: row for row in rows}

@app.get("/api/fees")
def get_fee_settings():
    """Seller and network fees (cents/kWh excl. VAT; VAT in %) used for the 'with fees' figures."""
    return {
        "values": fees.get_fees(),
        "defaults": fees.DEFAULTS,
        "labels": {k: label for k, (label, _) in fees.FIELDS.items()},
        "packages": fees.PACKAGES,
        "network_keys": list(fees.NETWORK_KEYS),
        "unit": "s/kWh",
    }

@app.put("/api/fees")
def put_fee_settings(payload: dict = Body(...)):
    try:
        return {"values": fees.save_fees(payload)}
    except ValueError as e:
        raise HTTPException(400, str(e))

def _sensor_settings(states: list[dict] | None = None) -> dict:
    """Sensors in use, their problems, and the HA sensors to pick from."""
    states = states if states is not None else ha.get_states()
    values, saved = sensors.load()
    entities = sorted(
        ({
            "entity_id": st["entity_id"],
            "name": st.get("attributes", {}).get("friendly_name"),
            "state": st.get("state"),
            "unit": st.get("attributes", {}).get("unit_of_measurement"),
            "power": ha.is_power_sensor(st),
            "price": "raw_today" in st.get("attributes", {}),   # what main reads from Nord Pool
        } for st in states or [] if st["entity_id"].startswith("sensor.")),
        key=lambda e: e["entity_id"],
    )
    return {
        "values": values,
        "defaults": sensors.DEFAULTS,
        "saved": saved,
        "labels": {k: label for k, (label, _, _) in sensors.FIELDS.items()},
        "kinds": {k: kind for k, (_, _, kind) in sensors.FIELDS.items()},
        "optional": sorted(sensors.OPTIONAL),
        "lists": sorted(sensors.LISTS),
        "problems": sensors.problems({st["entity_id"] for st in states}, values) if states is not None else [],
        "entities": entities,
        "ha_error": None if states is not None else "Can't reach Home Assistant",
    }

@app.get("/api/sensors")
def get_sensor_settings():
    """Home Assistant entities the tracker reads; picked in the UI or from the add-on options."""
    return _sensor_settings()

@app.put("/api/sensors")
def put_sensor_settings(payload: dict = Body(...)):
    states = ha.get_states()
    if states is None:
        raise HTTPException(502, "Can't reach Home Assistant to check the sensors")
    try:
        sensors.save(payload, {st["entity_id"] for st in states})
    except ValueError as e:
        raise HTTPException(400, str(e))
    print(f"🔌 Sensors picked in the UI: {sensors.describe()}")
    return _sensor_settings(states)

@app.delete("/api/sensors")
def reset_sensor_settings():
    sensors.reset()
    print(f"🔌 Sensors reset to the add-on options: {sensors.describe()}")
    return _sensor_settings()

@app.get("/api/version")
def get_version():
    """Installed version; the UI compares it with its own to spot a page cached from an older one."""
    return {"version": VERSION, "started_at": STARTED_AT}

@app.get("/api/price-sync")
def get_price_sync():
    """Status of the mFRR price sync from the Baltic Transparency Dashboard (on demand)."""
    return mffr_price_updater.sync_status

@app.get("/api/backfill")
def get_backfill_status():
    return backfill.status

@app.post("/api/backfill", status_code=202)
def start_backfill(payload: dict = Body(...)):
    """Rebuild slot rows in [from, to) from Home Assistant history (runs in the background)."""
    try:
        start = backfill.parse_local(payload["from"])
        end = backfill.parse_local(payload["to"])
    except (KeyError, ValueError, AttributeError):
        raise HTTPException(400, "Body must be {\"from\": ISO date/time, \"to\": ISO date/time}")
    if start >= end:
        raise HTTPException(400, "'to' must be after 'from'")
    try:
        return backfill.start_job(start, end)
    except RuntimeError as e:
        raise HTTPException(409, str(e))

@app.post("/api/qw-report")
async def import_qw_report(request: Request):
    """Import a Qilowatt balancing revenue or signals CSV (raw CSV as the request body)."""
    content = await request.body()
    if not content:
        raise HTTPException(400, "Send the CSV file as the request body")
    try:
        result = qw_report.import_report(content)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, f"Could not import report: {e}")
    if result["type"] == "revenue" and main.scheduler.running:
        # Official figures replace the estimate in the Energy dashboard statistics right away
        main.scheduler.get_job("payout_statistics").modify(next_run_time=datetime.now(LOCAL_TZ))
    return result

@app.get("/api/qw-report")
def get_qw_report(
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
):
    """Official per-slot figures from imported Qilowatt revenue reports."""
    return qw_report.report_slots(_normalize_to_local_iso(from_ts), _normalize_to_local_iso(to_ts))

class UIFiles(StaticFiles):
    """The built frontend. index.html is revalidated on every load, so an add-on update shows up
    right away; the files in assets/ have content hashes in their names and can stay cached."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response

# Serve the built frontend from the same container (must be mounted after the API routes)
if os.path.isdir(STATIC_DIR):
    app.mount("/", UIFiles(directory=STATIC_DIR, html=True), name="ui")
