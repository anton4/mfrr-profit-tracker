from datetime import date

import pytest
from sqlite_utils import Database

import entsoe_cbmp

NO_DATA = ("<Acknowledgement_MarketDocument><Reason><text>No matching data found</text></Reason>"
           "</Acknowledgement_MarketDocument>")


class _Response:
    status_code = 200
    text = NO_DATA


def test_the_25_hour_day_is_asked_for_in_two_parts(monkeypatch):
    asked = []
    monkeypatch.setattr(entsoe_cbmp, "ENTSOE_TOKEN", "token")
    monkeypatch.setattr(entsoe_cbmp.requests, "get",
                        lambda url, params, timeout: asked.append((params["periodStart"], params["periodEnd"]))
                        or _Response())

    entsoe_cbmp.fetch_day(date(2026, 10, 25))   # clocks went back: 25 hours
    assert asked == [("202610242100", "202610252100"), ("202610252100", "202610252200")]

    asked.clear()
    entsoe_cbmp.fetch_day(date(2026, 10, 26))
    assert asked == [("202610252200", "202610262200")]


def test_a_failing_day_does_not_hold_back_the_others(monkeypatch, tmp_path):
    db = Database(tmp_path / "cbmp.db")

    def fetch_day(day):
        if day == date(2026, 10, 25):
            raise RuntimeError("ENTSO-E: rejected")
        return [(1793000000, "UP", 100.0)]

    monkeypatch.setattr(entsoe_cbmp, "fetch_day", fetch_day)
    with pytest.raises(RuntimeError, match="rejected"):
        entsoe_cbmp.ensure_days({date(2026, 10, 25), date(2026, 10, 26)}, db)
    assert [d["day"] for d in db["afrr_cbmp_days"].rows] == ["2026-10-26"]


def test_rows_on_a_failed_day_wait_for_the_next_try(monkeypatch, tmp_path):
    db = Database(tmp_path / "cbmp.db")
    rows = [
        {"id": "a", "signal": "UP", "start": "2026-10-25T18:15:00+02:00", "end": "2026-10-25T18:20:00+02:00"},
        {"id": "b", "signal": "UP", "start": "2026-10-26T18:15:00+02:00", "end": "2026-10-26T18:20:00+02:00"},
    ]
    db["slots"].insert_all(rows, pk="id")

    def fetch_day(day):
        if day == date(2026, 10, 25):
            raise RuntimeError("ENTSO-E: rejected")
        return []

    monkeypatch.setattr(entsoe_cbmp, "fetch_day", fetch_day)
    with pytest.raises(RuntimeError):
        entsoe_cbmp.fill_rows(db, rows)
    assert db["slots"].get("a").get("cbmp_points") is None   # retried later
    assert db["slots"].get("b")["cbmp_points"] == 0
