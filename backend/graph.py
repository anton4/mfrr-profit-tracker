# graph.py — the power graph for any time window, read from Home Assistant (nothing is stored here).
#
# Up to DETAIL_SPAN, the recorded sensor states are replayed through the tracker's tick logic:
# 10 s points with the grid power, the Kratt command, the baseline and the requested power.
# Longer windows use Home Assistant's long-term statistics of the grid power sensors: 5-minute
# means while the recorder keeps them, hourly means after that. The Kratt commands come from
# the history of the Qilowatt source and mode sensors.
from datetime import datetime, timedelta, timezone

import pytz

import ha_statistics
import main
import sensors
from ha import DOWN_MODES, PROVIDER, UP_MODES, classify_market
from history import fetch_history

tz = pytz.timezone("Europe/Tallinn")
DETAIL_SPAN = timedelta(hours=6)
MAX_SPAN = timedelta(days=31)
# Home Assistant keeps 5-minute statistics as long as its history (purge_keep_days, 10 by default)
FIVE_MINUTE_DAYS = 9
FIVE_MINUTE_SPAN = timedelta(days=3)   # wider windows use hourly means


def window(start: datetime, end: datetime) -> dict:
    """Points and Kratt commands for [start, end]."""
    data = _detail(start, end) if end - start <= DETAIL_SPAN else _overview(start, end)
    return {"from": start.astimezone(tz).isoformat(), "to": end.astimezone(tz).isoformat(),
            "commands": commands(start, end), **data}


def _detail(start: datetime, end: datetime) -> dict:
    replay = main.replay_history(start, end)
    last = end.astimezone(tz)
    points = [p for p in replay.recent_points(start.astimezone(tz)) if datetime.fromisoformat(p["t"]) <= last]
    return {"resolution": "10s", "points": points, "notice": None}


def _overview(start: datetime, end: datetime) -> dict:
    phases = sensors.current()["grid_power"]
    old = start < datetime.now(timezone.utc) - timedelta(days=FIVE_MINUTE_DAYS)
    periods = ["hour"] if old or end - start > FIVE_MINUTE_SPAN else ["5minute", "hour"]
    for period in periods:
        stats = ha_statistics.statistics_during_period(phases, start, end, period)
        half = timedelta(minutes=2.5) if period == "5minute" else timedelta(minutes=30)
        buckets: dict[datetime, dict] = {}
        for phase in phases:
            for t, mean in stats.get(phase, []):
                if mean is not None:
                    buckets.setdefault(t, {})[phase] = mean
        # Net grid power: the phase means summed, where every phase has one; drawn mid-bucket
        points = [{"t": (t + half).astimezone(tz).isoformat(), "grid_w": round(sum(v.values()))}
                  for t, v in sorted(buckets.items()) if len(v) == len(phases)]
        if points:
            return {"resolution": "5min" if period == "5minute" else "1h", "points": points, "notice": None}
    return {"resolution": None, "points": [],
            "notice": "Home Assistant has no long-term statistics for the grid power sensors in this period "
                      "(they need state_class: measurement). Zoom in to 6 hours or less to see the recorded history."}


def _signal(source: str | None, mode: str | None) -> str | None:
    if (source or "").strip().lower() != PROVIDER:
        return None
    mode = (mode or "").strip().lower()
    return "DOWN" if mode in DOWN_MODES else "UP" if mode in UP_MODES else None


def commands(start: datetime, end: datetime) -> list[dict]:
    """Kratt commands overlapping [start, end] from the source and mode sensors' history."""
    sensor = sensors.current()
    if not sensor["source"] or not sensor["mode"]:
        return []
    changes = fetch_history([sensor["source"], sensor["mode"]], start, end)
    events = sorted([(t, "source", s) for t, s in changes.get(sensor["source"], [])] +
                    [(t, "mode", s) for t, s in changes.get(sensor["mode"], [])], key=lambda e: e[0])
    state = {"source": None, "mode": None}
    kratt_since = None    # when the source became Kratt: the run start that decides mFRR / aFRR
    runs, current = [], None
    for t, key, value in events:
        was_kratt = (state["source"] or "").strip().lower() == PROVIDER
        state[key] = value
        is_kratt = (state["source"] or "").strip().lower() == PROVIDER
        if is_kratt and not was_kratt:
            kratt_since = t
        signal = _signal(state["source"], state["mode"])
        if current and signal != current["signal"]:
            current["end"] = t
            runs.append(current)
            current = None
        if signal and current is None:
            current = {"start": t, "signal": signal,
                       "market": classify_market((kratt_since or t).astimezone(tz))}
    if current:
        current["end"] = end
        runs.append(current)
    return [{"start": max(r["start"], start).astimezone(tz).isoformat(), "end": min(r["end"], end).astimezone(tz).isoformat(),
             "signal": r["signal"], "market": r["market"]} for r in runs if r["end"] > start]
