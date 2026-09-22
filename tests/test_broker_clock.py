"""core/broker_clock.py -- the server clock both brokers run, New York close.

It replaced a hardcoded Europe/Bucharest on 2026-09-22. The two agree except in the weeks when
Europe and the US change clocks on different dates, which is exactly where these tests look.
"""

import pickle
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

import config.brokers as machine
from backtest.live_replay.market import server_wall, server_wall_to_utc
from core.broker_clock import BUCHAREST, NEW_YORK_CLOSE, BrokerClock

NY = ZoneInfo("America/New_York")


def test_new_york_close_is_new_york_plus_seven_hours_at_every_instant() -> None:
    """Every 15 minutes, 2007-2027: to the wall clock, back to UTC, and back again from a raw epoch."""
    t, end = datetime(2007, 1, 1, tzinfo=UTC), datetime(2028, 1, 1, tzinfo=UTC)
    while t < end:
        wall = t.astimezone(NEW_YORK_CLOSE)
        assert wall.replace(tzinfo=None) == t.astimezone(NY).replace(tzinfo=None) + timedelta(hours=7), t
        assert wall.astimezone(UTC) == t, t
        again = wall.replace(tzinfo=None).replace(tzinfo=NEW_YORK_CLOSE, fold=wall.fold)
        assert again.astimezone(UTC) == t, t
        assert datetime.fromtimestamp(t.timestamp(), NEW_YORK_CLOSE) == wall, t
        t += timedelta(minutes=15)


@pytest.mark.parametrize(("utc", "new_york_close", "bucharest"), [
    (datetime(2026, 9, 22, 12, 0, tzinfo=UTC), 3, 3),    # both on summer time: they agree
    (datetime(2026, 10, 28, 12, 0, tzinfo=UTC), 3, 2),   # Europe has left summer time, New York not
    (datetime(2026, 11, 3, 12, 0, tzinfo=UTC), 2, 2),    # both on winter time
    (datetime(2027, 3, 16, 12, 0, tzinfo=UTC), 3, 2),    # New York is on summer time, Europe not
])
def test_the_offsets_only_differ_in_the_weeks_the_two_rules_disagree(
    utc: datetime, new_york_close: int, bucharest: int
) -> None:
    assert utc.astimezone(NEW_YORK_CLOSE).utcoffset() == timedelta(hours=new_york_close)
    assert utc.astimezone(BUCHAREST).utcoffset() == timedelta(hours=bucharest)


def test_the_new_york_cash_open_is_1630_server_time_in_every_week() -> None:
    """The measurement config/brokers.py rests on, as a fact about the clock."""
    for day in ("2026-03-10", "2026-06-10", "2026-10-28", "2026-11-10"):
        open_ny = datetime.fromisoformat(f"{day} 09:30").replace(tzinfo=NY)
        assert open_ny.astimezone(NEW_YORK_CLOSE).strftime("%H:%M") == "16:30", day


def test_the_repeated_autumn_hour_keeps_both_readings_apart() -> None:
    """New York falls back at 06:00 UTC on 2026-11-01, so 08:00-08:59 server happens twice."""
    first = datetime(2026, 11, 1, 5, 30, tzinfo=UTC).astimezone(NEW_YORK_CLOSE)
    second = datetime(2026, 11, 1, 6, 30, tzinfo=UTC).astimezone(NEW_YORK_CLOSE)

    assert first.replace(tzinfo=None) == second.replace(tzinfo=None) == datetime(2026, 11, 1, 8, 30)
    assert (first.fold, second.fold) == (0, 1)
    assert (first.utcoffset(), second.utcoffset()) == (timedelta(hours=3), timedelta(hours=2))


def test_with_no_shift_it_is_the_zone_itself() -> None:
    zone = ZoneInfo("Europe/Bucharest")
    for utc in (datetime(2026, 1, 15, tzinfo=UTC), datetime(2026, 7, 15, tzinfo=UTC),
                datetime(2026, 10, 25, 0, 30, tzinfo=UTC), datetime(2026, 10, 25, 1, 30, tzinfo=UTC)):
        assert utc.astimezone(BUCHAREST).replace(tzinfo=None) == utc.astimezone(zone).replace(tzinfo=None)
        assert utc.astimezone(BUCHAREST).fold == utc.astimezone(zone).fold


@pytest.mark.parametrize(("text", "key", "shift"), [
    ("America/New_York+7", "America/New_York", 7),
    ("Europe/Bucharest", "Europe/Bucharest", 0),
    ("  America/New_York  ", "America/New_York", 0),
    ("Etc/GMT+2", "Etc/GMT+2", 0),  # a real key wins: IANA's Etc/GMT+2 is UTC-2, not GMT shifted +2
])
def test_parse_reads_the_form_key_prints(text: str, key: str, shift: int) -> None:
    clock = BrokerClock.parse(text)
    assert (clock.zone.key, clock.shift_hours) == (key, shift)
    assert BrokerClock.parse(clock.key) == clock


@pytest.mark.parametrize("text", ["", "New_York+7", "America/New_York+x", "Mars/Olympus"])
def test_parse_refuses_what_is_not_a_clock(text: str) -> None:
    with pytest.raises(ValueError):
        BrokerClock.parse(text)


def test_it_survives_pickling_and_compares_by_value() -> None:
    assert pickle.loads(pickle.dumps(NEW_YORK_CLOSE)) == NEW_YORK_CLOSE
    assert BrokerClock("America/New_York", 7) == NEW_YORK_CLOSE != BUCHAREST
    assert len({NEW_YORK_CLOSE, BrokerClock("America/New_York", 7), BUCHAREST}) == 2
    assert str(NEW_YORK_CLOSE) == NEW_YORK_CLOSE.key == "America/New_York+7"
    assert datetime(2026, 9, 22, tzinfo=NEW_YORK_CLOSE).tzname() == "+03"


def test_pandas_columns_convert_exactly_as_the_stdlib_does() -> None:
    """pandas cannot take a BrokerClock, so market.py goes through its zone and shift."""
    walls = pd.Series(pd.to_datetime(["2026-03-10 16:30", "2026-06-10 16:30", "2026-10-28 16:30",
                                      "2026-11-10 16:30", "2026-11-01 07:59", "2026-11-01 09:00"]))
    utc = server_wall_to_utc(walls, NEW_YORK_CLOSE)

    for wall, got in zip(walls, utc):
        expected = wall.to_pydatetime().replace(tzinfo=NEW_YORK_CLOSE).astimezone(UTC)
        assert got.to_pydatetime() == expected, wall
    epochs = (utc - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)
    assert list(server_wall(epochs.to_numpy(dtype=np.int64), NEW_YORK_CLOSE)) == list(walls)


class TestEachBrokersClock:
    def test_both_brokers_run_new_york_close(self) -> None:
        assert machine.SERVER_CLOCKS == {"fundingpips": NEW_YORK_CLOSE, "cfi": NEW_YORK_CLOSE}
        assert {p.name: p.clock for p in machine.profiles().values()} == machine.SERVER_CLOCKS

    def test_every_captured_broker_has_a_clock(self) -> None:
        assert set(machine.profiles()) == set(machine.SERVER_CLOCKS)

    @pytest.mark.parametrize(("path", "clock"), [
        (r"C:\tradebot\data\history\cfi\XAUUSD__M1.csv", NEW_YORK_CLOSE),
        ("data/history/cfi_portfolio/US100_Spot_M1.csv", NEW_YORK_CLOSE),
        ("data/history/cfi_oos/EURUSD__M1.csv", NEW_YORK_CLOSE),
        ("data/history/fundingpips/NDX100_M1.csv", NEW_YORK_CLOSE),
        ("data/history/NAS100_M1.csv", BUCHAREST),  # pre-CFI files: read as they always were
    ])
    def test_a_history_file_is_read_on_its_brokers_clock(self, path: str, clock: BrokerClock) -> None:
        assert machine.history_clock(path) == clock
