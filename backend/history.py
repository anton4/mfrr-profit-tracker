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
        resp = requests.get(_history_url(entity_ids, chunk_start, chunk_end), headers=_HEADERS, timeout=60)
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


def _history_url(entity_ids: list[str], start: datetime, end: datetime) -> str:
    return (
        f"{HA_URL}/api/history/period/{quote(start.isoformat())}"
        f"?end_time={quote(end.isoformat())}"
        f"&filter_entity_id={','.join(entity_ids)}"
        "&minimal_response&no_attributes&significant_changes_only=0"
    )


def has_states_at(entity_ids: list[str], t: datetime) -> set[str]:
    """The entities with a recorded state at `t`: HA answers each with its state in force then, or with nothing
    once the recorder has purged its last change before `t`. Raises on HTTP errors."""
    resp = requests.get(_history_url(entity_ids, t, t + timedelta(seconds=1)), headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    return {series[0].get("entity_id") for series in resp.json() if series} & set(entity_ids)


SEARCH_BACK = timedelta(days=400)        # further back than any recorder keeps states
SEARCH_STEP = timedelta(minutes=1)       # how close the search gets to the oldest state
RECORDED_CHECK = timedelta(hours=1)      # a sensor without a state this long ago isn't recorded


def oldest_history(entity_ids: list[str], now: datetime) -> dict:
    """From when HA's recorder has a state for every one of `entity_ids`. HA doesn't publish purge_keep_days, so
    the history itself is searched: whether all of them have a state at a moment flips once, at the oldest kept
    history. Returns history_from (None when a sensor has no history), limited_by (the sensors that set it) and
    missing (sensors with no recorded state at all). Raises on HTTP errors."""
    recent = now - RECORDED_CHECK
    missing = sorted(set(entity_ids) - has_states_at(entity_ids, recent))
    if missing:
        return {"history_from": None, "limited_by": [], "missing": missing}
    lo, hi = now - SEARCH_BACK, recent       # all present at hi; assumed not at lo
    if has_states_at(entity_ids, lo) >= set(entity_ids):
        return {"history_from": lo, "limited_by": [], "missing": []}
    while hi - lo > SEARCH_STEP:
        mid = lo + (hi - lo) / 2
        if has_states_at(entity_ids, mid) >= set(entity_ids):
            hi = mid
        else:
            lo = mid
    limited_by = sorted(set(entity_ids) - has_states_at(entity_ids, lo))
    return {"history_from": hi.replace(microsecond=0), "limited_by": limited_by, "missing": []}


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
