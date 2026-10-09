# sensors.py — which Home Assistant entities the tracker reads: the sensor_* add-on options
# (SENSOR_* in a standalone .env)
import os

# key: (label, env var, kind). The add-on option is the env var in lower case; power sensors
# are listed as suggestions when one is missing.
FIELDS = {
    "source":     ("Qilowatt source",      "SENSOR_SOURCE",     "text"),
    "mode":       ("Qilowatt mode",        "SENSOR_MODE",       "text"),
    "powerlimit": ("Qilowatt power limit", "SENSOR_POWERLIMIT", "power"),
    "grid_power": ("Grid power",           "SENSOR_GRID_POWER", "power"),
    "nordpool":   ("Nord Pool price",      "SENSOR_NORDPOOL",   "price"),
}
OPTIONAL = {"powerlimit"}
LISTS = {"grid_power"}   # one sensor per phase


def _env(key: str):
    value = os.getenv(FIELDS[key][1], "")
    if key in LISTS:
        return [e.strip() for e in value.split(",") if e.strip()]
    return value.strip()


VALUES = {key: _env(key) for key in FIELDS}


def current() -> dict:
    return VALUES


def option(key: str) -> str:
    """Add-on option name, e.g. sensor_grid_power."""
    return FIELDS[key][1].lower()


def ids_of(key: str) -> list[str]:
    """Entity IDs configured for one field (list fields have one per phase)."""
    value = VALUES[key]
    return list(value) if key in LISTS else [value] if value else []


def describe() -> str:
    """One log line: source=sensor.qw_source, mode=…"""
    return ", ".join(f"{key}={','.join(ids_of(key)) or '–'}" for key in FIELDS)


def problems(known_ids: set[str]) -> list[dict]:
    """Configured sensors that are missing or don't exist in Home Assistant."""
    found = []
    for key in FIELDS:
        ids = ids_of(key)
        if not ids and key not in OPTIONAL:
            found.append({"field": key, "option": option(key), "entity_id": None, "error": "not set"})
        for entity_id in ids:
            if entity_id not in known_ids:
                found.append({"field": key, "option": option(key), "entity_id": entity_id, "error": "not found"})
    return found
