# graph.py — the power graph for any time window, read from Home Assistant (nothing is stored here).
#
# Up to DETAIL_SPAN, the recorded sensor states are replayed through the tracker's tick logic:
# 10 s points with the grid power, the Kratt command, the baseline and the requested power.
# Longer windows use Home Assistant's long-term statistics of the grid power sensors: 5-minute
# means while the recorder keeps them, hourly means after that. The Kratt commands come from
# the history of the Qilowatt source, mode and power limit sensors: the direction is the grid
# target against the baseline, like the tracker's (ha.commanded_direction).
from datetime import datetime, timedelta, timezone
from itertools import groupby

import pytz

import ha_statistics
import main
import sensors
from ha import DOWN_MODES, PROVIDER, UP_MODES, classify_market, commanded_direction
from history import fetch_history

tz = pytz.timezone("Europe/Tallinn")
DETAIL_SPAN = timedelta(hours=6)
MAX_SPAN = timedelta(days=31)
# Home Assistant keeps 5-minute statistics as long as its history (purge_keep_days, 10 by default)
FIVE_MINUTE_DAYS = 9
FIVE_MINUTE_SPAN = timedelta(days=3)   # wider windows use hourly means


def window(start: datetime, end: datetime) -> dict:
    """Points and Kratt commands for [start, end]."""
    if end - start <= DETAIL_SPAN:
        data = _detail(start, end)
        baseline_at = _replayed_baseline(data["points"])
    else:
        data = _overview(start, end)
        baseline_at = _grid_before(data["points"], data["resolution"])
    return {"from": start.astimezone(tz).isoformat(), "to": end.astimezone(tz).isoformat(),
            "commands": commands(start, end, baseline_at), **data}


def _replayed_baseline(points: list[dict]):
    """baseline_at(t) from replayed 10 s points: the baseline the tracker locked for the run at t."""
    timed = [(datetime.fromisoformat(p["t"]), p.get("baseline_w")) for p in points]

    def baseline_at(t: datetime) -> float | None:
        for pt, baseline_w in timed:
            if pt >= t and baseline_w is not None:
                return baseline_w if (pt - t).total_seconds() <= 60 else None
        return None
    return baseline_at


def _grid_before(points: list[dict], resolution: str | None):
    """baseline_at(t) from statistics means: the grid power of the last bucket that ended by t."""
    half = timedelta(minutes=2.5) if resolution == "5min" else timedelta(minutes=30)
    ends = [(datetime.fromisoformat(p["t"]) + half, p["grid_w"]) for p in points]

    def baseline_at(t: datetime) -> float | None:
        before = [w for e, w in ends if e <= t and (t - e) <= 2 * half]
        return before[-1] if before else None
    return baseline_at


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


def _target(mode_signal: str | None, limit: str | None) -> float | None:
    """Kratt's grid target (W, +import / −export) from the unsigned power limit, as ha.get_target_w."""
    try:
        value = abs(float(limit))
    except (TypeError, ValueError):
        return None
    return value if mode_signal == "DOWN" else -value if mode_signal == "UP" else None


def commands(start: datetime, end: datetime, baseline_at=None) -> list[dict]:
    """Kratt commands overlapping [start, end] from the source, mode and power limit history.

    baseline_at(t) gives the grid power before a run that started at t; with it, the direction
    is the grid target against that baseline (an UP activation can come in frrdown mode).
    """
    sensor = sensors.current()
    if not sensor["source"] or not sensor["mode"]:
        return []
    keys = {sensor["source"]: "source", sensor["mode"]: "mode"}
    if sensor["powerlimit"] and baseline_at is not None:
        keys[sensor["powerlimit"]] = "limit"
    changes = fetch_history(list(keys), start, end)
    events = sorted([(t, key, s) for entity_id, key in keys.items() for t, s in changes.get(entity_id, [])],
                    key=lambda e: e[0])
    state = {"source": None, "mode": None, "limit": None}
    kratt_since = None    # when the source became Kratt: the run start that decides mFRR / aFRR
    baseline_w = None     # grid power before the run, locked while the source stays Kratt
    runs, current = [], None
    # Changes at the same moment (source, mode and limit together) are applied as one
    for t, group in groupby(events, key=lambda e: e[0]):
        was_kratt = (state["source"] or "").strip().lower() == PROVIDER
        for _, key, value in group:
            state[key] = value
        is_kratt = (state["source"] or "").strip().lower() == PROVIDER
        if is_kratt and not was_kratt:
            kratt_since = t
            baseline_w = baseline_at(t.astimezone(tz)) if baseline_at else None
        mode_signal = _signal(state["source"], state["mode"])
        signal = commanded_direction(mode_signal, _target(mode_signal, state["limit"]), baseline_w)
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
