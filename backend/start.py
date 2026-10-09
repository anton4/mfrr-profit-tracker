# start.py — container entry point.
#
# As a Home Assistant add-on, the Supervisor writes the add-on options to /data/options.json and
# provides SUPERVISOR_TOKEN. They're turned into the same environment variables the standalone
# (docker-compose + .env) setup uses, before any module reads them at import time.
# Every line written to stdout/stderr, uvicorn's included, gets a local timestamp.
import json
import os
import shutil
import sys
import threading
from datetime import datetime

import pytz
import uvicorn

OPTIONS_FILE = "/data/options.json"
IMPORT_DB = "/share/mfrr_tracker/mffr.db"   # database from a standalone install, copied once


class _Timestamped:
    """Stream wrapper that starts every line with the local time."""

    def __init__(self, stream, tz):
        self._stream = stream
        self._tz = tz
        self._line_start = True
        self._lock = threading.Lock()

    def write(self, text: str) -> int:
        with self._lock:
            out = []
            # print() writes the text and the newline separately: track where lines start
            for part in text.splitlines(keepends=True):
                if self._line_start:
                    out.append(datetime.now(self._tz).strftime("%Y-%m-%d %H:%M:%S "))
                out.append(part)
                self._line_start = part.endswith(("\n", "\r"))
            self._stream.write("".join(out))
            if self._line_start:
                self._stream.flush()   # whole lines right away, also without PYTHONUNBUFFERED
        return len(text)

    def __getattr__(self, name):
        return getattr(self._stream, name)


def timestamp_output() -> None:
    try:
        tz = pytz.timezone(os.getenv("TZ") or "Europe/Tallinn")
    except pytz.UnknownTimeZoneError:
        tz = pytz.utc
    sys.stdout = _Timestamped(sys.stdout, tz)
    sys.stderr = _Timestamped(sys.stderr, tz)


def _env_value(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(str(v).strip() for v in value if str(v).strip())
    return str(value)


def load_addon_options(path: str = OPTIONS_FILE) -> None:
    with open(path) as f:
        options = json.load(f)
    for key, value in options.items():
        value = _env_value(value)
        if value is not None:
            os.environ[key.upper()] = value
    # setdefault: HA_URL / HA_TOKEN can still be overridden for testing outside HA
    os.environ.setdefault("HA_URL", "http://supervisor/core")
    if os.getenv("SUPERVISOR_TOKEN"):
        os.environ.setdefault("HA_TOKEN", os.environ["SUPERVISOR_TOKEN"])
    os.environ.setdefault("DATA_DIR", "/data")
    os.environ.setdefault("LOG_DIR", "/data/logs")
    db_path = os.path.join(os.environ["DATA_DIR"], "mffr.db")
    if not os.path.exists(db_path) and os.path.isfile(IMPORT_DB):
        shutil.copyfile(IMPORT_DB, db_path)
        print(f"📥 Imported database from {IMPORT_DB}")
    print(f"🏠 Running as Home Assistant add-on (HA at {os.environ['HA_URL']}, data in {os.environ['DATA_DIR']})")


if __name__ == "__main__":
    timestamp_output()
    print(f"🚀 mFRR Profit Tracker {os.getenv('TRACKER_VERSION', 'dev')}")
    if os.path.isfile(OPTIONS_FILE):
        load_addon_options()
    # In-process, so uvicorn's log handlers write to the timestamped stderr
    uvicorn.run("api:app", host="0.0.0.0", port=8000)
