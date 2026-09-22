"""The clock check, and the entry it is allowed to refuse.

Written from two real events. On 2026-09-21 at 07:24 UTC the VPS's system clock jumped
two hours forward and Windows Time pulled it back 2m04s later; logs/trade_events.log
carries two future-dated polls and nothing else noticed. And mt5/rates.py's BROKER_TZ was
Europe/Bucharest until 2026-09-22, while both brokers run New York close: Bucharest leaves
DST on 2026-10-25, New York on 2026-11-01, so for that week every bar would have been an hour
off and the 09:30 opening range built out of 08:30 bars.
"""

from datetime import UTC, datetime, timedelta

import pytest

import mt5.clock as clock_mod
from core.broker_clock import BUCHAREST, NEW_YORK_CLOSE
from mt5.clock import MAX_PLAUSIBLE_HOURS, TOLERANCE_SECONDS, ClockVerdict, measure
from mt5.rates import BROKER_TZ

# Real UTC during the CFI session, well inside Bucharest's summer offset (UTC+3).
NOW = datetime(2026, 9, 21, 14, 15, 0, tzinfo=UTC)


def _tick(now: datetime, offset: timedelta = timedelta(0)) -> int:
    """The raw MT5 epoch a broker on BROKER_TZ would report at `now`, plus `offset`."""
    wall = now.astimezone(BROKER_TZ).replace(tzinfo=None) + offset
    return int(wall.replace(tzinfo=UTC).timestamp())


def test_a_broker_on_broker_tz_reads_as_agreement() -> None:
    v = measure("XAUUSD_", now=NOW, tick_time=_tick(NOW))

    assert (v.wrong, v.hours_off, v.measurable) == (False, 0, True)
    assert "uyğundur" in v.detail


def test_a_tick_a_few_seconds_old_is_still_agreement() -> None:
    """Ticks stop for a second on a thin symbol; that is not a clock fault."""
    v = measure("XAUUSD_", now=NOW, tick_time=_tick(NOW, timedelta(seconds=-90)))

    assert not v.wrong


def test_the_2026_09_21_two_hour_jump_is_caught() -> None:
    """The OS clock ran 2h fast, so the broker's real tick looks 2h BEHIND what
    BROKER_TZ predicts from that clock."""
    jumped = NOW + timedelta(hours=2)          # what the machine believed the time was
    v = measure("XAUUSD_", now=jumped, tick_time=_tick(NOW))   # what the broker really sent

    assert v.wrong
    assert v.hours_off == -2
    assert "SAAT" in v.detail


LATE_OCTOBER = datetime(2026, 10, 28, 14, 15, tzinfo=UTC)  # Bucharest on UTC+2, New York close on UTC+3


def _new_york_close_tick(now: datetime) -> int:
    """The raw MT5 epoch a New York close server -- both real brokers -- reports at `now`."""
    return int(now.astimezone(NEW_YORK_CLOSE).replace(tzinfo=UTC).timestamp())


def test_the_brokers_real_clock_agrees_in_the_week_bucharest_would_not() -> None:
    """The bug this project had: with the clock set right, 2026-10-28 is an ordinary day."""
    assert BROKER_TZ == NEW_YORK_CLOSE, "with no .env, and on both brokers, BROKER_TZ is New York close"

    v = measure("XAUUSD_", now=LATE_OCTOBER, tick_time=_new_york_close_tick(LATE_OCTOBER))

    assert (v.wrong, v.hours_off, v.measurable) == (False, 0, True)


def test_a_clock_left_on_bucharest_is_caught_in_that_week(monkeypatch) -> None:
    """2026-10-28: Bucharest is already on UTC+2, the New York close server still on UTC+3."""
    monkeypatch.setattr(clock_mod, "BROKER_TZ", BUCHAREST)
    assert LATE_OCTOBER.astimezone(BUCHAREST).utcoffset() == timedelta(hours=2), (
        "this test only means anything while Bucharest has left DST"
    )

    v = measure("XAUUSD_", now=LATE_OCTOBER, tick_time=_new_york_close_tick(LATE_OCTOBER))

    assert v.wrong
    assert v.hours_off == 1
    assert "Europe/Bucharest" in v.detail


def test_a_closed_market_is_not_reported_as_a_clock_fault() -> None:
    """A weekend leaves the last tick days behind. Refusing to trade over that would
    stop the bots every Sunday for a reason that is not a fault."""
    v = measure("XAUUSD_", now=NOW, tick_time=_tick(NOW, timedelta(hours=-50, minutes=-17)))

    assert not v.wrong
    assert not v.measurable
    assert "bazar" in v.detail


def test_an_implausibly_large_whole_hour_gap_is_a_closed_market_not_a_clock() -> None:
    """A weekend can land on a whole hour by coincidence -- 48h is a shut market, and no
    timezone error is ever that big."""
    v = measure("XAUUSD_", now=NOW, tick_time=_tick(NOW, timedelta(hours=-(MAX_PLAUSIBLE_HOURS + 1))))

    assert not v.wrong
    assert not v.measurable


def test_a_gap_that_is_not_near_a_whole_hour_is_not_judged() -> None:
    """Half an hour is neither a timezone nor the jump seen here; say so rather than
    guess, because a wrong refusal costs a real trade."""
    v = measure("XAUUSD_", now=NOW, tick_time=_tick(NOW, timedelta(minutes=-31)))

    assert not v.wrong
    assert not v.measurable


def test_no_tick_at_all_never_refuses(monkeypatch) -> None:
    """MT5 answers nothing on a symbol it has not selected yet. That is the connector's
    problem to report, not a reason to call the clock broken."""
    import mt5.clock as clock_mod

    monkeypatch.setattr(clock_mod.mt5, "symbol_info_tick", lambda s: None, raising=False)
    v = measure("XAUUSD_", now=NOW)

    assert not v.wrong
    assert v.drift is None and not v.measurable


def test_a_tick_is_read_from_mt5_when_none_is_given(monkeypatch) -> None:
    """The raw epoch, not a converted bar: rates_to_bars applies BROKER_TZ itself and
    would cancel the very error this looks for."""
    import mt5.clock as clock_mod

    raw = _tick(NOW, timedelta(hours=2))
    monkeypatch.setattr(clock_mod.mt5, "symbol_info_tick",
                        lambda s: type("T", (), {"time": raw})(), raising=False)
    v = measure("XAUUSD_", now=NOW)

    assert v.wrong and v.hours_off == 2


def test_tolerance_is_far_below_the_smallest_fault_it_must_catch() -> None:
    """The check is only meaningful while a quiet minute cannot be mistaken for an hour."""
    assert TOLERANCE_SECONDS < 3600 / 4


@pytest.mark.parametrize("hours", [-3, -2, -1, 1, 2, 3])
def test_every_plausible_whole_hour_error_is_caught(hours: int) -> None:
    v = measure("XAUUSD_", now=NOW, tick_time=_tick(NOW, timedelta(hours=hours)))

    assert v.wrong and v.hours_off == hours


class TestTheRunnersRefuseEntryOnly:
    """The guard must not become a reason an open position is left unmanaged."""

    def test_the_breakout_runner_blocks_entry_and_nothing_else(self, monkeypatch) -> None:
        import run_live_nasdaq_orb as runner

        monkeypatch.setattr(runner.clock, "measure",
                            lambda s: ClockVerdict(-7200.0, -2, True, "test"))
        assert runner._clock_trustworthy("XAUUSD_") is False

        monkeypatch.setattr(runner.clock, "measure",
                            lambda s: ClockVerdict(0.4, 0, True, "test"))
        assert runner._clock_trustworthy("XAUUSD_") is True

    def test_the_sweep_runner_uses_the_same_gate(self, monkeypatch) -> None:
        import run_live_xauusd_orb as runner

        monkeypatch.setattr(runner.clock, "measure",
                            lambda s: ClockVerdict(3600.0, 1, True, "test"))
        assert runner._clock_trustworthy("XAUUSD_") is False

    def test_an_unmeasurable_clock_never_stops_trading(self, monkeypatch) -> None:
        """Sunday: the last tick is two days old. The bots must still run."""
        import run_live_nasdaq_orb as runner

        monkeypatch.setattr(runner.clock, "measure",
                            lambda s: ClockVerdict(-180000.0, 0, False, "bazar bağlıdır"))
        assert runner._clock_trustworthy("XAUUSD_") is True
