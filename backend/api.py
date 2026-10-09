# api.py
import os
import threading
from contextlib import asynccontextmanager
from typing import Optional
from datetime import datetime

import pytz
import requests
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
import graph
import addon_config
import price_settings
import config
import entsoe_cbmp
import ha
import sensors

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("✅ Starting all schedulers from FastAPI")
    ha.log_sensor_check()
    price_settings.reconcile()   # a price or fee change since the last start applies now
    print("💶 aFRR market price (CBMP): "
          + ("ENTSO-E token set" if entsoe_cbmp.configured() else "ENTSO-E token not set, not shown"))
    main.write_current_timeslot()
    # The live view's last two hours, replayed from HA history without delaying the start
    threading.Thread(target=main.seed_live_points, daemon=True).start()
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

    # Variant with seller and network fees, with the fees in effect at each slot
    for row in rows:
        fees.add_fee_columns(row, price_settings.at(row["timeslot"])["fees"])

    # One row per slot, market and direction, keyed by id
    return {row["id"]: row for row in rows}

@app.get("/api/sensors")
def get_sensor_status():
    """Configured sensors, the ones Home Assistant doesn't have, and HA's sensors to pick from."""
    states = ha.get_states()
    entities = sorted(
        ({
            "entity_id": st["entity_id"],
            "name": st.get("attributes", {}).get("friendly_name"),
            "state": st.get("state"),
            "unit": st.get("attributes", {}).get("unit_of_measurement"),
        } for st in states or [] if st["entity_id"].startswith("sensor.")),
        key=lambda e: e["entity_id"],
    )
    return {
        "values": sensors.current(),
        "problems": sensors.problems({st["entity_id"] for st in states}) if states is not None else [],
        "entities": entities,
        "ha_error": None if states is not None else "Can't reach Home Assistant",
    }

def _through_ingress(request: Request) -> bool:
    return request.client is not None and request.client.host == addon_config.INGRESS_IP

def _config_access(request: Request) -> str | None:
    """Why the configuration can't be edited from this request, or None."""
    if not addon_config.available():
        return "Standalone install: the configuration is in .env. Restart the container after changing it."
    if not _through_ingress(request):
        return "Open the tracker from the Home Assistant sidebar to see and edit its configuration."
    return None

@app.get("/api/config")
def get_config(request: Request):
    """The add-on options (passwords masked) with the form fields and network packages."""
    if message := _config_access(request):
        return {"editable": False, "message": message}
    try:
        options = addon_config.masked(addon_config.get_options())
    except (ValueError, requests.RequestException) as e:
        raise HTTPException(502, f"Can't read the add-on configuration: {e}")
    return {
        "editable": True, "message": None,
        "options": options, "yaml": addon_config.to_yaml(options),
        "fields": addon_config.fields(), "mask": addon_config.MASK,
        # What an empty optional option means, shown as placeholders
        "defaults": {**{f"fee_{k}": v for k, (_, v) in fees.FIELDS.items()},
                     "mfrr_price_recheck_min": mffr_price_updater.DEFAULT_RECHECK_MIN,
                     "entsoe_cbmp_area": entsoe_cbmp.DEFAULT_AREA},
        # Võrk packages bring their own network rates (fee_elektrilevi_* only apply to custom)
        "packages": fees.PACKAGES, "network_options": [f"fee_{k}" for k in fees.NETWORK_KEYS],
    }

@app.post("/api/config/convert")
def convert_config(request: Request, payload: dict = Body(...)):
    """Switch the editor between form and YAML: {"options": …} → {"yaml": …} and back."""
    if message := _config_access(request):
        raise HTTPException(403, message)
    if "yaml" in payload:
        try:
            return {"options": addon_config.parse_yaml(str(payload["yaml"] or ""))}
        except ValueError as e:
            raise HTTPException(400, str(e))
    return {"yaml": addon_config.to_yaml(payload.get("options") or {})}

# POST rather than PUT: reverse proxies in front of Home Assistant often allow only GET and POST
@app.post("/api/config")
def save_config(request: Request, payload: dict = Body(...)):
    """Store the add-on options ({"options": …} from the form or {"yaml": …}) and restart."""
    if message := _config_access(request):
        raise HTTPException(403, message)
    try:
        current = addon_config.get_options()
    except (ValueError, requests.RequestException) as e:
        raise HTTPException(502, f"Can't read the add-on configuration: {e}")
    try:
        options = (addon_config.parse_yaml(str(payload["yaml"] or "")) if "yaml" in payload
                   else payload.get("options"))
        if not isinstance(options, dict):
            raise ValueError("No options to save")
        addon_config.save_options(addon_config.unmask(options, current))
        if payload.get("scope"):   # whether changed prices and fees apply to all history or from now on
            price_settings.set_pending(payload["scope"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    except requests.RequestException as e:
        raise HTTPException(502, f"Can't reach the Supervisor: {e}")
    print("⚙️ Configuration saved in the tracker's UI, restarting the add-on")
    addon_config.restart_soon()
    return {"restarting": True}

@app.get("/api/live")
def get_live():
    """Current Kratt command (direction, market, requested power) and the last two hours of ticks."""
    return main.live_status()

def _parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return dt if dt.tzinfo else LOCAL_TZ.localize(dt)

@app.get("/api/graph")
def get_graph(from_ts: str = Query(..., alias="from"), to_ts: str = Query(..., alias="to")):
    """Grid power and Kratt commands for any window, from Home Assistant's history and statistics."""
    try:
        start, end = _parse_time(from_ts), _parse_time(to_ts)
    except ValueError:
        raise HTTPException(400, "from and to must be ISO date-times")
    end = min(end, datetime.now(LOCAL_TZ))
    if start >= end:
        raise HTTPException(400, "'from' must be before 'to' and in the past")
    if end - start > graph.MAX_SPAN:
        raise HTTPException(400, f"The window can be at most {graph.MAX_SPAN.days} days")
    try:
        return graph.window(start, end)
    except Exception as e:
        raise HTTPException(502, f"Can't read Home Assistant history: {e}")

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
