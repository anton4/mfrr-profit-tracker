# addon_config.py — the add-on options, read and written through the Supervisor API.
#
# The options are the only place the tracker's configuration is stored. Home Assistant's
# Configuration tab edits them, and so does the tracker's UI (Data tools → Configuration) as
# YAML. The add-on reads them on start, so saving restarts it.
import os
import threading
import time

import requests
import yaml

SUPERVISOR_URL = os.getenv("SUPERVISOR_URL", "http://supervisor")
# Home Assistant's Ingress proxy: only requests through it may see or change the options,
# not the unauthenticated direct port
INGRESS_IP = os.getenv("INGRESS_IP", "172.30.32.2")
PASSWORD_KEYS = {"entsoe_token"}
MASK = "********"   # stands in for a password in the YAML sent to the browser


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


def to_yaml(options: dict) -> str:
    """Options as YAML for the browser, passwords masked."""
    shown = {k: MASK if k in PASSWORD_KEYS and v else v for k, v in options.items()}
    return yaml.safe_dump(shown, sort_keys=False, allow_unicode=True, default_flow_style=False)


def from_yaml(text: str, current: dict) -> dict:
    """Options from YAML edited in the browser; a password left masked keeps its stored value.
    Raises ValueError on bad YAML."""
    try:
        options = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"YAML error: {e}")
    if not isinstance(options, dict) or not all(isinstance(k, str) for k in options):
        raise ValueError("The configuration must be a list of 'option: value' lines")
    for key in PASSWORD_KEYS:
        if options.get(key) == MASK:
            if current.get(key):
                options[key] = current[key]
            else:
                del options[key]
    return options
