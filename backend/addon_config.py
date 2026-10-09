# addon_config.py — the add-on options, read and written through the Supervisor API.
#
# The options are the only place the tracker's configuration is stored. Home Assistant's
# Configuration tab edits them, and so does the tracker's UI (Data tools → Configuration), as a
# form or as YAML. The add-on reads them on start, so saving restarts it.
import os
import re
import threading
import time
from functools import lru_cache

import requests
import yaml

SUPERVISOR_URL = os.getenv("SUPERVISOR_URL", "http://supervisor")
# Home Assistant's Ingress proxy: only requests through it may see or change the options,
# not the unauthenticated direct port
INGRESS_IP = os.getenv("INGRESS_IP", "172.30.32.2")
PASSWORD_KEYS = {"entsoe_token"}
MASK = "********"   # stands in for a password in what the browser gets
# The add-on's config.yaml and translations: copied into the image, or the repo's add-on folder
_HERE = os.path.dirname(os.path.abspath(__file__))
ADDON_DIR = next((d for d in (os.path.join(_HERE, "addon"), os.path.join(_HERE, "..", "mfrr_tracker"))
                  if os.path.isfile(os.path.join(d, "config.yaml"))), None)


def available() -> bool:
    """Running as an add-on, with the Supervisor API at hand."""
    return bool(os.getenv("SUPERVISOR_TOKEN"))


def _call(method: str, path: str, timeout: float = 15, **kwargs) -> dict:
    """Supervisor API call. Raises ValueError with the Supervisor's message when it refuses,
    requests.RequestException when it can't be reached."""
    resp = requests.request(method, f"{SUPERVISOR_URL}{path}", timeout=timeout,
                            headers={"Authorization": f"Bearer {os.environ['SUPERVISOR_TOKEN']}"}, **kwargs)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not resp.ok or body.get("result") == "error":
        raise ValueError(body.get("message") or f"Supervisor returned {resp.status_code}")
    return body.get("data") or {}


def get_options() -> dict:
    return _call("GET", "/addons/self/info").get("options") or {}


def save_options(options: dict) -> None:
    """Store the options; the Supervisor validates them against the add-on schema."""
    _call("POST", "/addons/self/options", json={"options": options})


def restart_soon(delay_s: float = 1.0) -> None:
    """Restart the add-on after the current response has gone out (the restart stops us)."""
    def restart():
        time.sleep(delay_s)
        try:
            _call("POST", "/addons/self/restart", timeout=60)
        except Exception as e:
            print(f"❌ Add-on restart failed: {e}")
    threading.Thread(target=restart, daemon=True).start()


@lru_cache(maxsize=1)
def fields() -> list[dict]:
    """The options schema as form fields, in config.yaml order, named by translations/en.yaml."""
    if not ADDON_DIR:
        return []
    with open(os.path.join(ADDON_DIR, "config.yaml")) as f:
        schema = yaml.safe_load(f).get("schema") or {}
    names = {}
    translations = os.path.join(ADDON_DIR, "translations", "en.yaml")
    if os.path.isfile(translations):
        with open(translations) as f:
            names = (yaml.safe_load(f) or {}).get("configuration") or {}
    result = []
    for key, typ in schema.items():
        field = {"key": key, "name": names.get(key, {}).get("name", key),
                 "description": names.get(key, {}).get("description"), "entity": key.startswith("sensor_")}
        if isinstance(typ, list):   # e.g. [str]: one value per row
            field.update(kind="list", optional=str(typ[0]).endswith("?"))
        else:
            kind, arg = re.fullmatch(r"(\w+)(?:\((.*)\))?", typ.rstrip("?")).groups()
            field["optional"] = typ.endswith("?")
            if kind == "list":
                field.update(kind="select", choices=arg.split("|"))
            elif kind in ("float", "int"):
                field["kind"] = "number"
                if arg:
                    low, high = (v.strip() for v in arg.split(","))
                    field.update(min=float(low) if low else None, max=float(high) if high else None)
            else:
                field["kind"] = {"bool": "bool", "password": "password"}.get(kind, "text")
        result.append(field)
    return result


def masked(options: dict) -> dict:
    return {k: MASK if k in PASSWORD_KEYS and v else v for k, v in options.items()}


def unmask(options: dict, current: dict) -> dict:
    """A password left masked keeps its stored value."""
    options = dict(options)
    for key in PASSWORD_KEYS:
        if options.get(key) == MASK:
            if current.get(key):
                options[key] = current[key]
            else:
                del options[key]
    return options


def to_yaml(options: dict) -> str:
    return yaml.safe_dump(options, sort_keys=False, allow_unicode=True, default_flow_style=False)


def parse_yaml(text: str) -> dict:
    """Options from YAML edited in the browser. Raises ValueError on bad YAML."""
    try:
        options = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"YAML error: {e}")
    if not isinstance(options, dict) or not all(isinstance(k, str) for k in options):
        raise ValueError("The configuration must be a list of 'option: value' lines")
    return options
