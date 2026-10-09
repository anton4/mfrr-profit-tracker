# sensors.py — which Home Assistant entities the tracker reads
#
# Defaults come from the add-on options / .env (SENSOR_*). Entities picked in the UI
# (Data tools → Sensors) are saved in the settings table and take precedence.
import json
import os

from sqlite_utils import Database

import config

DB_PATH = config.DB_PATH

# key: (label, env var, kind). Kinds steer the picker: power = W/kW sensors, price = Nord Pool.
FIELDS = {
    "source":     ("Qilowatt source",      "SENSOR_SOURCE",     "text"),
    "mode":       ("Qilowatt mode",        "SENSOR_MODE",       "text"),
    "powerlimit": ("Qilowatt power limit", "SENSOR_POWERLIMIT", "power"),
    "grid_power": ("Grid power",           "SENSOR_GRID_POWER", "power"),
    "nordpool":   ("Nord Pool price",      "SENSOR_NORDPOOL",   "price"),
}
OPTIONAL = {"powerlimit"}
LISTS = {"grid_power"}   # one sensor per phase


def _split(value: str) -> list[str]:
    return [e.strip() for e in value.split(",") if e.strip()]


def _env(key: str):
    value = os.getenv(FIELDS[key][1], "")
    return _split(value) if key in LISTS else value.strip()


DEFAULTS = {key: _env(key) for key in FIELDS}

_current = None   # effective sensors, cached for the 10 s ticks


def _saved(db: Database) -> dict | None:
    if "settings" not in db.table_names():
        return None
    try:
        return json.loads(db["settings"].get("sensors")["value"])
    except Exception:
        return None


def load(db: Database | None = None) -> tuple[dict, bool]:
    """(effective sensors, whether they were picked in the UI)."""
    saved = _saved(db or Database(DB_PATH))
    values = dict(DEFAULTS)
    if saved:
        values.update({k: v for k, v in saved.items() if k in FIELDS})
    return values, saved is not None


def current() -> dict:
    global _current
    if _current is None:
        _current = load()[0]
    return _current


def ids_of(key: str, values: dict | None = None) -> list[str]:
    """Entity IDs configured for one field (list fields have one per phase)."""
    value = (values or current())[key]
    return list(value) if key in LISTS else [value] if value else []


def describe(values: dict | None = None) -> str:
    """One log line: source=sensor.qw_source, mode=…"""
    return ", ".join(f"{key}={','.join(ids_of(key, values)) or '–'}" for key in FIELDS)


def save(values: dict, known_ids: set[str], db: Database | None = None) -> dict:
    """Validate and store sensors picked in the UI. Raises ValueError on bad input."""
    global _current
    clean = {}
    for key, (label, _, _) in FIELDS.items():
        value = values.get(key)
        if key in LISTS:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"{label}: expected a list of entity IDs")
            value = [v.strip() for v in value if v.strip()]
            if not value:
                raise ValueError(f"{label}: pick at least one sensor")
            if len(set(value)) != len(value):
                raise ValueError(f"{label}: the same sensor is picked twice")
            ids = value
        else:
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{label}: expected an entity ID")
            value = (value or "").strip()
            if not value and key not in OPTIONAL:
                raise ValueError(f"{label}: pick a sensor")
            ids = [value] if value else []
        for entity_id in ids:
            if entity_id not in known_ids:
                raise ValueError(f"{label}: {entity_id} doesn't exist in Home Assistant")
        clean[key] = value
    db = db or Database(DB_PATH)
    db["settings"].upsert({"key": "sensors", "value": json.dumps(clean)}, pk="key")
    _current = clean
    return clean


def reset(db: Database | None = None) -> dict:
    """Forget the sensors picked in the UI, so the add-on options apply again."""
    global _current
    db = db or Database(DB_PATH)
    if "settings" in db.table_names():
        db["settings"].delete_where("key = ?", ["sensors"])
    _current = dict(DEFAULTS)
    return _current


def problems(known_ids: set[str], values: dict | None = None) -> list[dict]:
    """Configured sensors that are missing or don't exist in Home Assistant."""
    found = []
    for key in FIELDS:
        ids = ids_of(key, values)
        if not ids and key not in OPTIONAL:
            found.append({"field": key, "entity_id": None, "error": "not set"})
        for entity_id in ids:
            if entity_id not in known_ids:
                found.append({"field": key, "entity_id": entity_id, "error": "not found"})
    return found
