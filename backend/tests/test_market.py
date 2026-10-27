from datetime import datetime, timedelta
from pathlib import Path

import pytz
from sqlite_utils import Database

import main
import qw_report
from ha import classify_market

tz = pytz.timezone("Europe/Tallinn")
SIGNALS_2026_10_26 = Path(__file__).parent / "fixtures" / "qw_signals_2026-10-26.csv"


def local(text: str) -> datetime:
    return tz.localize(datetime.fromisoformat(text))


def test_mfrr_runs_start_a_minute_before_or_right_at_a_quarter():
    assert classify_market(local("2026-10-20 10:14:00")) == "MFRR"
    assert classify_market(local("2026-10-26 11:00:00")) == "MFRR"   # Qilowatt: MFRR
    assert classify_market(local("2026-10-26 13:00:00")) == "MFRR"   # Qilowatt: MFRR
    assert classify_market(local("2026-10-26 12:50:01")) == "AFRR"   # Qilowatt: AFRR
    assert classify_market(local("2026-10-26 23:37:26")) == "AFRR"   # Qilowatt: AFRR


def test_a_run_back_within_a_minute_keeps_the_previous_market():
    tracker = main.Tracker(store_baseline=False)
    assert tracker._resumed_market(local("2026-10-26 13:15:24")) is None
    tracker.last_run = (local("2026-10-26 13:15:04"), "MFRR")
    assert tracker._resumed_market(local("2026-10-26 13:15:24")) == "MFRR"
    assert tracker._resumed_market(local("2026-10-26 13:16:30")) is None


def test_report_market_reads_mixed_rows_as_mfrr():
    assert qw_report.report_market("MFRR") == "MFRR"
    assert qw_report.report_market("MFRR, AFRR") == "MFRR"
    assert qw_report.report_market("AFRR") == "AFRR"
    assert qw_report.report_market("") is None
    assert qw_report.report_market(None) is None


def test_signals_import_corrects_the_market_of_mixed_slots():
    db = Database(main.DB_PATH)
    slots = [local("2026-10-26 11:00") + timedelta(minutes=15 * i) for i in range(32)] + [local("2026-10-26 23:30")]
    for slot in slots:
        key = slot.isoformat()
        db["slots"].upsert({
            "id": main.slot_id(key, "AFRR", "UP"), "timeslot": key, "market": "AFRR", "signal": "UP",
            "start": key, "end": key, "slot_end": (slot + timedelta(minutes=15)).isoformat(),
            "energy_kwh": 1.0, "mffr_price": 158.0, "price_source": "estimate",
        }, pk="id", alter=True)

    result = qw_report.import_report(SIGNALS_2026_10_26.read_bytes(), db)

    markets = {r["timeslot"][11:16]: r["market"] for r in db["slots"].rows if r["timeslot"].startswith("2026-10-26")}
    assert result["type"] == "signals"
    for hhmm in ("11:00", "11:45", "12:00", "13:15", "15:15", "16:00", "17:15", "18:45"):
        assert markets[hhmm] == "MFRR", hhmm
    assert markets["12:45"] == "AFRR"   # only AFRR rows (12:50)
    assert markets["23:30"] == "AFRR"
    corrected = db["slots"].get(main.slot_id(local("2026-10-26 11:45").isoformat(), "AFRR", "UP"))
    assert corrected["mffr_price"] is None   # the BTD sync fills the mFRR price
