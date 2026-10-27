from datetime import datetime, timedelta
from urllib.parse import parse_qs, unquote, urlparse

import pytest
import pytz

import backfill
import history

tz = pytz.timezone("Europe/Tallinn")
NOW = tz.localize(datetime(2026, 10, 27, 12, 7))
SOURCE, MODE, GRID = "sensor.qw_source", "sensor.qw_mode", "sensor.grid_power"


class FakeHistory:
    """HA's history API over recorded states that start at `oldest` per entity (None: not recorded)."""

    def __init__(self, oldest: dict):
        self.oldest = oldest
        self.calls = 0

    def get(self, url, headers=None, timeout=None):
        self.calls += 1
        parsed = urlparse(url)
        t = datetime.fromisoformat(unquote(parsed.path.rsplit("/", 1)[1]))
        entities = parse_qs(parsed.query)["filter_entity_id"][0].split(",")
        series = [[{"entity_id": e, "state": "1", "last_changed": t.isoformat()}]
                  for e in entities if self.oldest.get(e) is not None and self.oldest[e] <= t]
        return _Response(series)


class _Response:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


@pytest.fixture
def fake(monkeypatch):
    def install(oldest):
        ha = FakeHistory(oldest)
        monkeypatch.setattr(history.requests, "get", ha.get)
        return ha
    return install


def test_the_oldest_history_is_the_latest_start_among_the_sensors(fake):
    purge = tz.localize(datetime(2026, 10, 17, 4, 12, 30))
    ha = fake({SOURCE: purge - timedelta(days=3), MODE: purge - timedelta(days=1), GRID: purge})
    found = history.oldest_history([SOURCE, MODE, GRID], NOW)
    assert purge <= found["history_from"] <= purge + timedelta(minutes=1)
    assert found["limited_by"] == [GRID] and found["missing"] == []
    assert ha.calls < 30


def test_a_sensor_the_recorder_skips_is_missing(fake):
    fake({SOURCE: NOW - timedelta(days=10), MODE: NOW - timedelta(days=10), GRID: None})
    found = history.oldest_history([SOURCE, MODE, GRID], NOW)
    assert found == {"history_from": None, "limited_by": [], "missing": [GRID]}


@pytest.fixture
def needs(monkeypatch):
    monkeypatch.setattr(backfill, "_needed_entities", lambda: [SOURCE, MODE, GRID])
    backfill._range_cache.update(key=None, at=None, value=None)


def test_a_backfill_starts_at_the_first_full_quarter_with_history(fake, needs):
    purge = tz.localize(datetime(2026, 10, 17, 4, 12, 30))
    fake({SOURCE: purge, MODE: purge, GRID: purge})
    r = backfill.available_range()
    assert r["earliest"] == tz.localize(datetime(2026, 10, 17, 4, 15)).isoformat()

    backfill.check_start(tz.localize(datetime(2026, 10, 17, 4, 15)))
    with pytest.raises(ValueError, match=r"history from 17\.10\.2026 04:1\d, so a backfill can start at "
                                         r"17\.10\.2026 04:15"):
        backfill.check_start(tz.localize(datetime(2026, 10, 17, 4, 0)))
    # refused before any history is fetched
    with pytest.raises(ValueError):
        backfill.run_backfill(tz.localize(datetime(2026, 10, 1)), tz.localize(datetime(2026, 10, 2)))


def test_the_first_slot_rounds_up_to_a_quarter():
    at = lambda h, m, s=0: tz.localize(datetime(2026, 10, 17, h, m, s))   # noqa: E731
    assert backfill._ceil_slot(at(4, 15)) == at(4, 15)
    assert backfill._ceil_slot(at(4, 15, 1)) == at(4, 30)
    assert backfill._ceil_slot(at(23, 50)) == tz.localize(datetime(2026, 10, 18, 0, 0))


def test_a_sensor_without_history_blocks_the_backfill(fake, needs):
    fake({SOURCE: NOW - timedelta(days=10), MODE: NOW - timedelta(days=10), GRID: None})
    with pytest.raises(ValueError, match="records no history for sensor.grid_power"):
        backfill.check_start(NOW - timedelta(days=1))


def test_the_range_is_cached_and_an_error_does_not_block(fake, needs, monkeypatch):
    ha = fake({SOURCE: NOW - timedelta(days=10), MODE: NOW - timedelta(days=10), GRID: NOW - timedelta(days=10)})
    backfill.available_range()
    calls = ha.calls
    backfill.available_range()
    assert ha.calls == calls

    def broken(*args, **kwargs):
        raise ConnectionError("HA unreachable")

    monkeypatch.setattr(history.requests, "get", broken)
    r = backfill.available_range(force=True)
    assert r["earliest"] is None and "HA unreachable" in r["error"]
    backfill.check_start(NOW - timedelta(days=60))   # unknown: not refused here
