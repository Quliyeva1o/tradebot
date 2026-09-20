"""Tests for strategy/or_fib_pullback.py -- the opening range's golden-pocket pullback.

Bars are built in New York wall-clock time on 2026-09-14 (EDT, UTC-4) and handed to the
strategy as UTC, the way MT5Connector.fetch_recent_bars() delivers them. Every day uses
the same 100-110 opening range, so the hand-worked levels are the same throughout:

    long  -- entry 110 - 0.618 * 10 = 103.82, zone 102.14, stop 102.14 - 0.02 * 10 = 101.94
    short -- entry 100 + 0.618 * 10 = 106.18, zone 107.86, stop 108.06
"""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from core.models import Bar, SignalDirection
from strategy.or_fib_pullback import (
    STRATEGY_TAG,
    DayState,
    OpeningRange,
    OrFibPullbackConfig,
    atr_ok,
    opening_range,
    setup_id,
)

NY = ZoneInfo("America/New_York")
CFG = OrFibPullbackConfig(atr_mult=0.0)

LONG_ENTRY, LONG_STOP = 103.82, 101.94
SHORT_ENTRY, SHORT_STOP = 106.18, 108.06


def _bar(hh: int, mm: int, o: float, h: float, low: float, c: float) -> Bar:
    ts = datetime(2026, 9, 14, hh, mm, tzinfo=NY).astimezone(UTC)
    return Bar(timestamp=ts, open=o, high=h, low=low, close=c, volume=100.0)


def _or_bars() -> list[Bar]:
    """A 100-110 opening range: the 09:30 bar the rule requires, plus the extremes."""
    return [
        _bar(9, 30, 105, 106, 104, 105),
        _bar(9, 35, 105, 110, 104, 109),   # the high
        _bar(9, 44, 109, 109, 100, 101),   # the low
    ]


def _feed(bars: list[Bar], cfg: OrFibPullbackConfig = CFG):
    """Runs a whole day through DayState and returns the first entry, or None."""
    or_range = opening_range(bars, cfg)
    assert or_range is not None
    state = DayState(or_range, cfg)
    for bar in bars:
        entry = state.on_bar(bar)
        if entry is not None:
            return entry
    return None


class TestOpeningRange:
    def test_high_and_low_come_from_the_first_fifteen_minutes(self) -> None:
        assert opening_range(_or_bars(), CFG) == OpeningRange(high=110.0, low=100.0)

    def test_bars_outside_the_window_do_not_widen_it(self) -> None:
        bars = [_bar(9, 29, 105, 500, 1, 105), *_or_bars(), _bar(9, 45, 101, 400, 2, 112)]

        assert opening_range(bars, CFG) == OpeningRange(high=110.0, low=100.0)

    def test_day_without_the_0930_bar_is_skipped(self) -> None:
        assert opening_range(_or_bars()[1:], CFG) is None

    def test_flat_range_is_skipped(self) -> None:
        assert opening_range([_bar(9, 30, 100, 100, 100, 100)], CFG) is None

    def test_level_and_extension_read_the_same_line_from_opposite_ends(self) -> None:
        r = OpeningRange(high=110, low=100)

        assert r.level(0.618, long=True) == pytest.approx(r.extension(0.382, long=True))
        assert r.level(0.618, long=False) == pytest.approx(r.extension(0.382, long=False))
        assert r.extension(1.272, long=True) == pytest.approx(112.72)   # beyond the high
        assert r.extension(1.272, long=False) == pytest.approx(97.28)   # beyond the low


class TestAtrFilter:
    RANGE = OpeningRange(high=110, low=100)  # span 10

    def test_range_wider_than_the_threshold_passes(self) -> None:
        assert atr_ok(self.RANGE, 8.0, OrFibPullbackConfig(atr_mult=1.0))

    def test_range_narrower_than_the_threshold_fails(self) -> None:
        assert not atr_ok(self.RANGE, 12.0, OrFibPullbackConfig(atr_mult=1.0))

    def test_missing_atr_fails_rather_than_waving_the_day_through(self) -> None:
        assert not atr_ok(self.RANGE, None, OrFibPullbackConfig(atr_mult=1.0))

    def test_zero_multiplier_disables_the_filter_even_without_an_atr(self) -> None:
        assert atr_ok(self.RANGE, None, OrFibPullbackConfig(atr_mult=0.0))


class TestLimitEntry:
    def test_long_break_then_pullback_into_the_pocket(self) -> None:
        bars = [*_or_bars(),
                _bar(9, 45, 101, 112, 101, 112),    # closes above 110 -> long break
                _bar(9, 46, 112, 120, 103, 104)]    # dips to the 0.618 level

        entry = _feed(bars)

        assert entry is not None
        assert entry.direction == SignalDirection.BUY
        assert entry.price == pytest.approx(LONG_ENTRY)
        assert entry.stop == pytest.approx(LONG_STOP)
        assert entry.risk == pytest.approx(1.88)
        # The target is the morning high KNOWN BEFORE the entry bar: 112, not this bar's 120.
        assert entry.target == pytest.approx(112.0)
        assert entry.morning_extreme == pytest.approx(112.0)
        assert entry.break_time == datetime(2026, 9, 14, 13, 45, tzinfo=UTC)
        assert entry.time == datetime(2026, 9, 14, 13, 46, tzinfo=UTC)

    def test_short_break_then_pullback_into_the_pocket(self) -> None:
        bars = [*_or_bars(),
                _bar(9, 45, 101, 101, 98, 99),      # closes below 100 -> short break
                _bar(9, 46, 99, 107, 99, 100)]      # rallies to the 0.618 level

        entry = _feed(bars)

        assert entry is not None
        assert entry.direction == SignalDirection.SELL
        assert entry.price == pytest.approx(SHORT_ENTRY)
        assert entry.stop == pytest.approx(SHORT_STOP)
        assert entry.target == pytest.approx(98.0)   # the morning low before this bar

    def test_the_break_bar_itself_cannot_fill(self) -> None:
        # The break is a CLOSE beyond the range, so it is only known once the bar is over.
        bars = [*_or_bars(),
                _bar(9, 45, 101, 112, 103, 112),    # dips into the pocket AND breaks
                _bar(9, 46, 112, 113, 111, 112)]    # never comes back

        assert _feed(bars) is None

    def test_only_one_trade_a_day(self) -> None:
        or_range = opening_range(_or_bars(), CFG)
        assert or_range is not None
        state = DayState(or_range, CFG)
        bars = [*_or_bars(),
                _bar(9, 45, 101, 112, 101, 112),
                _bar(9, 46, 112, 113, 103, 104),    # the entry
                _bar(9, 47, 104, 113, 103, 112)]    # would fill again

        entries = [e for e in (state.on_bar(b) for b in bars) if e is not None]

        assert len(entries) == 1

    def test_the_first_break_takes_the_day(self) -> None:
        bars = [*_or_bars(),
                _bar(9, 45, 101, 101, 98, 99),      # short break first
                _bar(9, 46, 99, 113, 99, 112),      # closes above the range, but too late
                _bar(9, 47, 112, 112, 106, 107)]

        entry = _feed(bars)

        assert entry is not None
        assert entry.direction == SignalDirection.SELL

    def test_nothing_is_entered_after_the_deadline(self) -> None:
        bars = [*_or_bars(),
                _bar(9, 45, 101, 112, 101, 112),
                _bar(12, 0, 112, 113, 103, 104)]    # would fill, but the deadline has passed

        assert _feed(bars) is None


class TestRejectionEntry:
    CFG = OrFibPullbackConfig(atr_mult=0.0, require_rejection=True)

    def test_a_close_still_inside_the_pocket_is_not_an_entry(self) -> None:
        bars = [*_or_bars(),
                _bar(9, 45, 101, 112, 101, 112),
                _bar(9, 46, 112, 112, 103, 103.5)]  # touches the pocket, closes in it

        assert _feed(bars, self.CFG) is None

    def test_entry_is_the_close_that_rejects_the_pocket(self) -> None:
        bars = [*_or_bars(),
                _bar(9, 45, 101, 112, 101, 112),
                _bar(9, 46, 112, 112, 103, 103.5),   # the test
                _bar(9, 47, 103.5, 105, 103.4, 104.5)]  # the rejection

        entry = _feed(bars, self.CFG)

        assert entry is not None
        assert entry.price == pytest.approx(104.5)   # a market fill at that close
        assert entry.stop == pytest.approx(LONG_STOP)
        assert entry.target == pytest.approx(112.0)
        assert entry.time == datetime(2026, 9, 14, 13, 47, tzinfo=UTC)

    def test_a_close_out_of_the_pocket_without_a_test_first_is_not_an_entry(self) -> None:
        bars = [*_or_bars(),
                _bar(9, 45, 101, 112, 101, 112),
                _bar(9, 46, 112, 113, 110, 111)]     # never reaches the pocket

        assert _feed(bars, self.CFG) is None

    def test_running_past_the_stop_before_any_rejection_kills_the_setup(self) -> None:
        bars = [*_or_bars(),
                _bar(9, 45, 101, 112, 101, 112),
                _bar(9, 46, 112, 112, 101.5, 102),   # through 101.94 while waiting
                _bar(9, 47, 102, 113, 102, 112)]     # a rejection close, but too late

        assert _feed(bars, self.CFG) is None


class TestStopAndTargetVariants:
    BARS = [*_or_bars(), _bar(9, 45, 101, 112, 101, 112), _bar(9, 46, 112, 113, 103, 104)]

    def test_stop_at_the_swing_extreme_is_the_range_edge(self) -> None:
        entry = _feed(self.BARS, OrFibPullbackConfig(atr_mult=0.0, stop_at_swing_extreme=True))

        assert entry is not None
        assert entry.stop == pytest.approx(100.0)
        assert entry.risk == pytest.approx(3.82)

    def test_extension_target_projects_beyond_the_range(self) -> None:
        entry = _feed(self.BARS, OrFibPullbackConfig(atr_mult=0.0, target_extension_fib=1.272))

        assert entry is not None
        assert entry.target == pytest.approx(112.72)

    def test_r_target_is_measured_from_the_fill_not_the_fib_level(self) -> None:
        # Fill 103.82, stop 101.94 -> risk 1.88, so a 1R target sits at 105.70.
        entry = _feed(self.BARS, OrFibPullbackConfig(atr_mult=0.0, target_r=1.0))

        assert entry is not None
        assert entry.target == pytest.approx(105.70)
        assert entry.target - entry.price == pytest.approx(entry.risk)

    def test_r_target_follows_a_wider_stop(self) -> None:
        cfg = OrFibPullbackConfig(atr_mult=0.0, target_r=1.0, stop_at_swing_extreme=True)

        entry = _feed(self.BARS, cfg)

        assert entry is not None
        assert (entry.stop, entry.risk) == pytest.approx((100.0, 3.82))
        assert entry.target == pytest.approx(107.64)

    def test_short_r_target_sits_below_the_fill(self) -> None:
        bars = [*_or_bars(), _bar(9, 45, 101, 101, 98, 99), _bar(9, 46, 99, 107, 99, 100)]

        entry = _feed(bars, OrFibPullbackConfig(atr_mult=0.0, target_r=1.0))

        assert entry is not None
        assert entry.target == pytest.approx(104.30)   # 106.18 - 1.88

    def test_zero_buffer_puts_the_stop_exactly_on_the_far_fib(self) -> None:
        entry = _feed(self.BARS, OrFibPullbackConfig(atr_mult=0.0, stop_buffer_frac=0.0))

        assert entry is not None
        assert entry.stop == pytest.approx(102.14)


class TestConfig:
    def test_a_pocket_with_its_edges_crossed_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            OrFibPullbackConfig(entry_fib=0.786, zone_fib=0.618)

    def test_an_extension_inside_the_range_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            OrFibPullbackConfig(target_extension_fib=0.9)

    def test_two_targets_at_once_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            OrFibPullbackConfig(target_extension_fib=1.272, target_r=1.0)

    def test_a_non_positive_r_target_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            OrFibPullbackConfig(target_r=0.0)

    def test_a_cancel_time_before_the_entry_deadline_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            OrFibPullbackConfig(entry_deadline=time(12, 0), close_at=time(11, 0))

    def test_holding_to_the_stop_or_target_is_allowed(self) -> None:
        assert OrFibPullbackConfig(close_at=None).close_at is None


class TestSetupId:
    def test_tag_fits_the_twenty_characters_mt5_keeps(self) -> None:
        assert len(STRATEGY_TAG) <= 20

    def test_id_carries_the_date_and_side(self) -> None:
        entry = _feed([*_or_bars(), _bar(9, 45, 101, 112, 101, 112),
                       _bar(9, 46, 112, 113, 103, 104)])
        assert entry is not None

        assert setup_id("NDX100", entry) == f"{STRATEGY_TAG}_NDX100_20260914_BUY"
