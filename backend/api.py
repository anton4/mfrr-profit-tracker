# api.py
import os
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

app = FastAPI()
DB_FILE = "data/mffr.db"
STATIC_DIR = "static"   # built frontend (copied in by the Dockerfile)

LOCAL_TZ = pytz.timezone(os.getenv("TZ", "Europe/Tallinn"))

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

    # One row per slot and direction, keyed by id ("<timeslot>_<signal>")
    return {row["id"]: row for row in rows}

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
        return qw_report.import_report(content)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, f"Could not import report: {e}")

@app.get("/api/qw-report")
def get_qw_report(
    from_ts: Optional[str] = Query(None, alias="from"),
    to_ts: Optional[str] = Query(None, alias="to"),
):
    """Official per-slot figures from imported Qilowatt revenue reports."""
    return qw_report.report_slots(_normalize_to_local_iso(from_ts), _normalize_to_local_iso(to_ts))

@app.on_event("startup")
def start_all_schedulers():
    print("✅ Starting all schedulers from FastAPI")
    main.write_current_timeslot()
    profit_calc.run_profit_calculation()
    mffr_price_updater.fetch_and_update_mffr_prices()
    if not main.scheduler.running:
        main.scheduler.start()
    if not profit_calc.scheduler.running:
        profit_calc.scheduler.start()
    if not mffr_price_updater.scheduler.running:
        mffr_price_updater.scheduler.start()

# Serve the built frontend from the same container (must be mounted after the API routes)
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
