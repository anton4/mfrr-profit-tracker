# ha.py — shared Home Assistant access + Kratt signal detection
import os
import requests

HA_URL = os.getenv("HA_URL", "http://localhost:8123")
HA_TOKEN = os.getenv("HA_TOKEN")

# Entities (from .env)
SENSOR_SOURCE = os.environ["SENSOR_SOURCE"]           # sensor.qw_source (== "kratt" during an mFRR command)
SENSOR_MODE = os.environ["SENSOR_MODE"]               # QW mode sensor: BUY → DOWN, SELL/FRRUP → UP
SENSOR_GRID = os.environ["SENSOR_GRID"]               # grid power (W, +import / -export)
SENSOR_NORDPOOL = os.environ["SENSOR_NORDPOOL"]       # nordpool price (€/kWh)

PROVIDER = "kratt"
DOWN_MODES = {"buy"}
UP_MODES = {"sell", "frrup"}

_HEADERS = {"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"}


def get_entity(entity_id: str) -> dict | None:
    """Full HA state object (state + attributes), or None on failure."""
    try:
        resp = requests.get(f"{HA_URL}/api/states/{entity_id}", headers=_HEADERS, timeout=5)
        if not resp.ok:
            print(f"❌ Failed to fetch {entity_id}: {resp.status_code}")
            return None
        return resp.json()
    except Exception as e:
        print(f"❌ Error fetching {entity_id}: {e}")
        return None


def get_state(entity_id: str) -> str | None:
    entity = get_entity(entity_id)
    state = entity.get("state") if entity else None
    return None if state in ("unknown", "unavailable", None) else state


def get_float(entity_id: str) -> float | None:
    state = get_state(entity_id)
    try:
        return float(state) if state is not None else None
    except ValueError:
        return None


def get_signal() -> str | None:
    """'UP' / 'DOWN' while Kratt is in control, otherwise None."""
    source = get_state(SENSOR_SOURCE)
    if not source or source.strip().lower() != PROVIDER:
        return None
    mode = (get_state(SENSOR_MODE) or "").strip().lower()
    if mode in DOWN_MODES:
        return "DOWN"
    if mode in UP_MODES:
        return "UP"
    return None


def mffr_power_w(signal: str, grid_w: float, baseline_w: float) -> float:
    """Kratt meters at the grid connection point; only count deviation in the commanded direction."""
    if signal == "DOWN":
        return max(0.0, grid_w - baseline_w)   # extra import
    if signal == "UP":
        return max(0.0, baseline_w - grid_w)   # extra export / reduced import
    return 0.0
