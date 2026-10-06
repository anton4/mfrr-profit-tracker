# ha.py — shared Home Assistant access + Kratt signal detection
import os
from datetime import datetime

import requests

HA_URL = os.getenv("HA_URL", "http://localhost:8123")
HA_TOKEN = os.getenv("HA_TOKEN")

# Entities (from .env)
SENSOR_SOURCE = os.environ["SENSOR_SOURCE"]           # sensor.qw_source (== "Kratt" during an mFRR command)
SENSOR_MODE = os.environ["SENSOR_MODE"]               # sensor.qw_mode: frrdown → DOWN, frrup → UP
SENSOR_POWERLIMIT = os.getenv("SENSOR_POWERLIMIT")    # sensor.qw_powerlimit: requested power (optional)
# Grid power per phase (W, +import / -export), comma-separated (e.g. Shelly 3EM channel a/b/c power)
SENSOR_GRID_POWER = [e.strip() for e in os.environ["SENSOR_GRID_POWER"].split(",") if e.strip()]
SENSOR_NORDPOOL = os.environ["SENSOR_NORDPOOL"]       # nordpool price (€/kWh)

PROVIDER = "kratt"
DOWN_MODES = {"frrdown"}
UP_MODES = {"frrup"}

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


# Every reader takes an optional `fetch` (entity_id → HA state dict). Live reads use get_entity;
# a backfill passes a reader over recorded HA history, so the same logic replays the past.

def get_state(entity_id: str, fetch=None) -> str | None:
    entity = (fetch or get_entity)(entity_id)
    state = entity.get("state") if entity else None
    return None if state in ("unknown", "unavailable", None) else state


def _get_scaled(entity_id: str, units: dict, default_unit: str, fetch=None) -> float | None:
    """Numeric state converted via the entity's unit_of_measurement."""
    entity = (fetch or get_entity)(entity_id)
    if not entity or entity.get("state") in ("unknown", "unavailable", None):
        return None
    unit = (entity.get("attributes", {}).get("unit_of_measurement") or default_unit).strip().lower()
    try:
        return float(entity["state"]) * units.get(unit, 1.0)
    except ValueError:
        return None


def get_requested_w(fetch=None) -> float | None:
    """Power Kratt requested for the current command (W, unsigned)."""
    if not SENSOR_POWERLIMIT:
        return None
    value = _get_scaled(SENSOR_POWERLIMIT, _POWER_UNITS, "W", fetch)
    return abs(value) if value is not None else None


def get_grid_power_w(fetch=None) -> float | None:
    """Net grid power summed over all phases (W, +import / -export).

    Summing signed phase powers nets the phases like a phase-summing utility meter,
    so one phase importing while another exports does not count as both.
    """
    total = 0.0
    for entity_id in SENSOR_GRID_POWER:
        value = _get_scaled(entity_id, _POWER_UNITS, "W", fetch)
        if value is None:
            return None   # a partial sum would misstate the grid flow
        total += value
    return total


class GridMeter:
    """Net grid energy by integrating the summed phase power between reads (trapezoidal)."""

    # Gaps up to this long are interpolated between the two readings
    MAX_GAP_S = 60
    # Longer gaps (HA unreachable, sensors unavailable) are bridged only if the power on both
    # sides is the same within this tolerance: the power is then assumed constant in between
    BRIDGE_TOLERANCE_W = 100
    BRIDGE_TOLERANCE_PCT = 3

    def __init__(self):
        self._prev = None   # (power_w, datetime)

    def _steady(self, a: float, b: float) -> bool:
        tolerance = max(self.BRIDGE_TOLERANCE_W, max(abs(a), abs(b)) * self.BRIDGE_TOLERANCE_PCT / 100.0)
        return abs(a - b) <= tolerance

    def read(self, now, slot_start=None, fetch=None):
        """(net_kwh, seconds) since the previous successful read; net is +import / −export.

        A bridged long gap is clipped to slot_start so a long outage doesn't pile earlier
        slots' energy into the current one. Returns None on the first read, a failed read,
        or a long gap across which the power changed.
        """
        power_w = get_grid_power_w(fetch)
        if power_w is None:
            return None   # keep the last good reading for bridging
        prev, self._prev = self._prev, (power_w, now)
        if prev is None:
            return None
        start = prev[1]
        if (now - start).total_seconds() > self.MAX_GAP_S:
            if not self._steady(prev[0], power_w):
                return None
            if slot_start is not None and start < slot_start:
                start = slot_start
        seconds = (now - start).total_seconds()
        if seconds <= 0:
            return None
        return (prev[0] + power_w) / 2.0 * seconds / 3_600_000.0, seconds


def get_source_changed(fetch=None):
    """When qw_source last changed (the start of the current Kratt run), or None."""
    entity = (fetch or get_entity)(SENSOR_SOURCE)
    ts = (entity or {}).get("last_changed")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def classify_market(run_start: datetime) -> str:
    """mFRR vs aFRR from the run's start time.

    Scheduled mFRR activations start exactly one minute before a quarter (hh:14/29/44/59:00)
    and are updated every ~4 min; aFRR starts at arbitrary times, is updated about every
    minute and ends with a ~5 min restore. Matched all 18 runs in a Qilowatt signals report.
    """
    if run_start.minute % 15 == 14 and run_start.second <= 20:
        return "MFRR"
    return "AFRR"


def get_signal(fetch=None) -> str | None:
    """'UP' / 'DOWN' while Kratt is in control, otherwise None."""
    source = get_state(SENSOR_SOURCE, fetch)
    if not source or source.strip().lower() != PROVIDER:
        return None
    mode = (get_state(SENSOR_MODE, fetch) or "").strip().lower()
    if mode in DOWN_MODES:
        return "DOWN"
    if mode in UP_MODES:
        return "UP"
    return None


def deviation_direction(deviation_kwh: float) -> str | None:
    """Kratt splits regulated energy by the sign of (grid − baseline): above the baseline
    (more import / less export) is DOWN, below is UP — whatever the command says."""
    if deviation_kwh > 0:
        return "DOWN"
    if deviation_kwh < 0:
        return "UP"
    return None
