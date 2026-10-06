# ha.py — shared Home Assistant access + Kratt signal detection
import os
import requests

HA_URL = os.getenv("HA_URL", "http://localhost:8123")
HA_TOKEN = os.getenv("HA_TOKEN")

# Entities (from .env)
SENSOR_SOURCE = os.environ["SENSOR_SOURCE"]           # sensor.qw_source (== "Kratt" during an mFRR command)
SENSOR_MODE = os.environ["SENSOR_MODE"]               # sensor.qw_mode: frrdown → DOWN, frrup → UP
SENSOR_POWERLIMIT = os.getenv("SENSOR_POWERLIMIT")    # sensor.qw_powerlimit: requested power (optional)
# Cumulative grid energy counters, comma-separated (e.g. one per Shelly 3EM phase)
SENSOR_GRID_IMPORT = [e.strip() for e in os.environ["SENSOR_GRID_IMPORT"].split(",") if e.strip()]
SENSOR_GRID_EXPORT = [e.strip() for e in os.environ["SENSOR_GRID_EXPORT"].split(",") if e.strip()]
SENSOR_NORDPOOL = os.environ["SENSOR_NORDPOOL"]       # nordpool price (€/kWh)

PROVIDER = "kratt"
DOWN_MODES = {"frrdown"}
UP_MODES = {"frrup"}

_ENERGY_UNITS = {"wh": 0.001, "kwh": 1.0, "mwh": 1000.0}
_POWER_UNITS = {"w": 1.0, "kw": 1000.0, "mw": 1_000_000.0}

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


def _get_scaled(entity_id: str, units: dict, default_unit: str) -> float | None:
    """Numeric state converted via the entity's unit_of_measurement."""
    entity = get_entity(entity_id)
    if not entity or entity.get("state") in ("unknown", "unavailable", None):
        return None
    unit = (entity.get("attributes", {}).get("unit_of_measurement") or default_unit).strip().lower()
    try:
        return float(entity["state"]) * units.get(unit, 1.0)
    except ValueError:
        return None


def get_energy_kwh(entity_id: str) -> float | None:
    return _get_scaled(entity_id, _ENERGY_UNITS, "kWh")


def get_requested_w() -> float | None:
    """Power Kratt requested for the current command (W, unsigned)."""
    if not SENSOR_POWERLIMIT:
        return None
    value = _get_scaled(SENSOR_POWERLIMIT, _POWER_UNITS, "W")
    return abs(value) if value is not None else None


def _sum_counters(entity_ids: list[str]) -> float | None:
    total = 0.0
    for entity_id in entity_ids:
        value = get_energy_kwh(entity_id)
        if value is None:
            return None   # a partial sum would look like a huge drop/jump
        total += value
    return total


class GridMeter:
    """Net grid energy from cumulative import/export counters.

    Phases are netted per read (Σ import − Σ export), like a phase-summing utility meter,
    so one phase importing while another exports does not count as both.
    """

    def __init__(self):
        self._prev = None   # (import_kwh, export_kwh, datetime)

    def read(self, now):
        """(net_kwh, seconds) since the previous successful read; net is +import / −export.
        Returns None on the first read, a failed read or a counter reset."""
        imp = _sum_counters(SENSOR_GRID_IMPORT)
        exp = _sum_counters(SENSOR_GRID_EXPORT)
        if imp is None or exp is None:
            return None
        prev, self._prev = self._prev, (imp, exp, now)
        if prev is None:
            return None
        d_imp, d_exp = imp - prev[0], exp - prev[1]
        seconds = (now - prev[2]).total_seconds()
        if d_imp < 0 or d_exp < 0 or seconds <= 0:
            return None   # counter reset (e.g. Shelly reboot)
        return d_imp - d_exp, seconds


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


def mffr_energy_kwh(signal: str, net_kwh: float, seconds: float, baseline_w: float) -> float:
    """Kratt meters at the grid connection point: delivered energy is the metered net grid energy
    vs. the baseline over the same time, counted only in the commanded direction."""
    baseline_kwh = baseline_w / 1000.0 * seconds / 3600.0
    if signal == "DOWN":
        return max(0.0, net_kwh - baseline_kwh)   # extra import
    if signal == "UP":
        return max(0.0, baseline_kwh - net_kwh)   # extra export / reduced import
    return 0.0
