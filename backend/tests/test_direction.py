from datetime import datetime, timedelta

import pytest
import pytz
from sqlite_utils import Database

import main
import sensors
from ha import commanded_direction, regulation_w

tz = pytz.timezone("Europe/Tallinn")


def local(text: str) -> datetime:
    return tz.localize(datetime.fromisoformat(text))


def test_an_up_activation_in_frrdown_mode_is_up():
    # 2026-11-21 12:30: charging from the grid at 18.8 kW, Kratt cut the import to 10.8 kW
    assert commanded_direction("DOWN", 10_800, 18_834) == "UP"
    assert regulation_w(10_800, 18_834) == pytest.approx(8_034)


def test_the_target_against_the_baseline_decides():
    assert commanded_direction("UP", -15_500, 11_240) == "UP"     # 2026-11-21 09:15, frrup
    assert commanded_direction("DOWN", 5_100, -2_300) == "DOWN"   # 2026-10-26 15:18, frrdown


def test_the_mode_decides_a_hold_at_the_baseline_or_without_a_target():
    assert commanded_direction("UP", -2_300, -2_300) == "UP"      # 2026-10-26 16:00–19:00
    assert commanded_direction("DOWN", 2_900, 2_800) == "DOWN"    # within the hold tolerance
    assert commanded_direction("DOWN", None, 18_834) == "DOWN"    # no power limit sensor
    assert commanded_direction("UP", -5_000, None) == "UP"
    assert commanded_direction(None, 10_800, 18_834) is None      # Kratt not in control
    assert regulation_w(None, 18_834) is None


@pytest.fixture
def ha_sensors(monkeypatch):
    monkeypatch.setitem(sensors.VALUES, "source", "sensor.qw_source")
    monkeypatch.setitem(sensors.VALUES, "mode", "sensor.qw_mode")
    monkeypatch.setitem(sensors.VALUES, "powerlimit", "sensor.qw_powerlimit")
    monkeypatch.setitem(sensors.VALUES, "grid_power", ["sensor.grid_power"])


def states(source: str, mode: str, limit_w: float, grid_w: float, source_changed: datetime):
    entities = {
        "sensor.qw_source": {"state": source, "last_changed": source_changed.isoformat()},
        "sensor.qw_mode": {"state": mode},
        "sensor.qw_powerlimit": {"state": str(limit_w), "attributes": {"unit_of_measurement": "W"}},
        "sensor.grid_power": {"state": str(grid_w), "attributes": {"unit_of_measurement": "W"}},
    }
    return entities.get


def test_the_tracker_books_an_up_activation_in_frrdown_mode_as_up(ha_sensors):
    # Replays 2026-11-21 12:27–12:40 like a backfill: the optimizer charges in frrdown mode at
    # 18.8 kW import, then Kratt takes over at 12:30:03 in the same mode with a 10.8 kW limit
    db = Database(main.DB_PATH)
    tracker = main.Tracker(store_baseline=False, keep_points=True)
    run_start = local("2026-11-21 12:30:03")
    t = local("2026-11-21 12:27:00")
    while t < local("2026-11-21 12:40:00"):
        if t < run_start:
            fetch = states("qilowatt", "frrdown", 16_500, 18_834, local("2026-11-21 12:00:00"))
        else:
            fetch = states("kratt", "frrdown", 10_800, 10_800, run_start)
        tracker.tick(t, fetch=fetch, write=True, live=False, db=db)
        t += timedelta(seconds=10)

    key = local("2026-11-21 12:30").isoformat()
    rows = {r["signal"]: r for r in db["slots"].rows_where("timeslot = ?", [key])}
    assert set(rows) == {"UP"}
    up = rows["UP"]
    assert up["market"] == "MFRR"
    assert up["baseline_w"] == pytest.approx(18_834)
    # About 10 min of 8 kW less import: delivered as requested
    assert up["energy_kwh"] == pytest.approx(8.034 * up["metered_s"] / 3600, rel=0.02)
    assert up["requested_kwh"] == pytest.approx(up["energy_kwh"], rel=0.05)
    assert 95 <= up["delivery_pct"] <= 105

    point = tracker.points[-1]
    assert point["signal"] == "UP"
    assert point["requested_w"] == pytest.approx(8_034, abs=1)


def test_graph_commands_take_the_direction_from_the_target(ha_sensors, monkeypatch):
    import graph
    utc = pytz.utc
    at = lambda text: local(text).astimezone(utc)
    history = {
        "sensor.qw_source": [(at("2026-11-21 12:00:00"), "qilowatt"), (at("2026-11-21 12:30:03"), "kratt"),
                             (at("2026-11-21 19:30:05"), "qilowatt")],
        "sensor.qw_mode": [(at("2026-11-21 12:00:00"), "frrdown")],
        "sensor.qw_powerlimit": [(at("2026-11-21 12:00:00"), "16500"), (at("2026-11-21 12:30:03"), "10700")],
    }
    monkeypatch.setattr(graph, "fetch_history", lambda ids, start, end: {i: history.get(i, []) for i in ids})
    start, end = local("2026-11-21 12:00"), local("2026-11-21 20:00")

    # 5-minute means: 18.8 kW import before the run
    points = [{"t": "2026-11-21T12:22:30+02:00", "grid_w": 18_834}, {"t": "2026-11-21T12:27:30+02:00", "grid_w": 18_834}]
    runs = graph.commands(start, end, graph._grid_before(points, "5min"))
    assert [(r["signal"], r["market"]) for r in runs] == [("UP", "MFRR")]
    assert runs[0]["start"].startswith("2026-11-21T12:30:03") and runs[0]["end"].startswith("2026-11-21T19:30:05")

    # Without a baseline the mode decides, as before
    assert [r["signal"] for r in graph.commands(start, end)] == ["DOWN"]
