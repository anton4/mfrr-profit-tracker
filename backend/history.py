# history.py — read recorded state history from the Home Assistant REST API for backfills
from bisect import bisect_right
from datetime import datetime, timedelta
from urllib.parse import quote

import requests

from ha import HA_URL, _HEADERS, get_entity

CHUNK = timedelta(hours=6)   # keeps each response small (phase power changes every few seconds)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def fetch_history(entity_ids: list[str], start: datetime, end: datetime, progress=None) -> dict:
    """{entity_id: [(time, state), ...]} sorted by time, covering [start, end].

    HA returns each entity's state at the chunk start first, so the state at any moment is
    the last change at or before it. Raises on HTTP errors.
    """
    changes = {e: [] for e in entity_ids}
    chunk_start = start
    total_s = max(1.0, (end - start).total_seconds())
    while chunk_start < end:
        chunk_end = min(chunk_start + CHUNK, end)
        url = (
            f"{HA_URL}/api/history/period/{quote(chunk_start.isoformat())}"
            f"?end_time={quote(chunk_end.isoformat())}"
            f"&filter_entity_id={','.join(entity_ids)}"
            "&minimal_response&no_attributes&significant_changes_only=0"
        )
        resp = requests.get(url, headers=_HEADERS, timeout=60)
        resp.raise_for_status()
        for series in resp.json():
            if not series:
                continue
            entity_id = series[0].get("entity_id")
            if entity_id not in changes:
                continue
            for item in series:
                ts = item.get("last_changed") or item.get("last_updated")
                if ts:
                    changes[entity_id].append((_parse_time(ts), item.get("state")))
        chunk_start = chunk_end
        if progress:
            progress((chunk_start - start).total_seconds() / total_s)

    for entity_id, items in changes.items():
        items.sort(key=lambda x: x[0])
    return changes


class HistoryStates:
    """HA-style state reader over recorded history at a moving replay time.

    Units come from each entity's current state (history fetched with no_attributes).
    """

    def __init__(self, changes: dict, units: dict):
        self._times = {e: [t for t, _ in items] for e, items in changes.items()}
        self._states = {e: [s for _, s in items] for e, items in changes.items()}
        self._units = units
        self.now = None

    def fetch(self, entity_id: str) -> dict | None:
        times = self._times.get(entity_id)
        if not times or self.now is None:
            return None
        i = bisect_right(times, self.now) - 1
        if i < 0:
            return None   # no state recorded yet at this time
        unit = self._units.get(entity_id)
        return {"state": self._states[entity_id][i],
                "last_changed": self._times[entity_id][i].isoformat(),
                "attributes": {"unit_of_measurement": unit} if unit else {}}


def current_units(entity_ids: list[str]) -> dict:
    units = {}
    for entity_id in entity_ids:
        entity = get_entity(entity_id) or {}
        unit = entity.get("attributes", {}).get("unit_of_measurement")
        if unit:
            units[entity_id] = unit
    return units
