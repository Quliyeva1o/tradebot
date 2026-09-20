"""Tests for strategy/ny_fib_retest.py -- the 09:30-10:00 range's Fibonacci retest.

Bars are built in New York wall-clock time on 2026-09-14 (EDT, UTC-4) and handed to
the strategy as UTC, the way MT5Connector.fetch_recent_bars() delivers them. Every
range below is 100 to 110, so the hand-worked levels are the same throughout:

    midline (0.5) 105.0
    long  -- stop 100 + 0.236 * 10 = 102.36, target 100 + 1.618 * 10 = 116.18
    short -- stop 110 - 0.236 * 10 = 107.64, target 110 - 1.618 * 10 =  93.82
"""

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from core.models import Bar, SignalDirection
from strategy.ny_fib_retest import (
    STRATEGY_TAG,
    NyFibRetestConfig,
    fib_level,
    find_plan,
    order_expires_at,
    setup_id,
)

NY = ZoneInfo("America/New_York")


def _bar(hh: int, mm: int, o: float, h: float, low: float, c: float) -> Bar:
    ts = datetime(2026, 9, 14, hh, mm, tzinfo=NY).astimezone(UTC)
    return Bar(timestamp=ts, open=o, high=h, low=low, close=c, volume=100.0)


def _range_bars() -> list[Bar]:
    """A 100-110 range: the 09:30 opening bar the rule requires, plus the extremes."""
    return [
        _bar(9, 30, 104, 106, 103, 105),
        _bar(9, 45, 105, 110, 104, 108),   # the high
        _bar(9, 59, 108, 109, 100, 101),   # the low
    ]


class TestBias:
    def test_close_above_the_midline_is_a_long(self) -> None:
        bars = [*_range_bars(), _bar(10, 0, 101, 108, 101, 107)]

        plan = find_plan(bars, NyFibRetestConfig())

        assert plan is not None
        assert plan.direction == SignalDirection.BUY
        assert plan.range_high == 110.0
        assert plan.range_low == 100.0
        assert plan.bias_price == 107.0
        assert plan.entry == pytest.approx(105.0)
        assert plan.stop == pytest.approx(102.36)
        assert plan.target == pytest.approx(116.18)
        assert plan.risk == pytest.approx(2.64)
        # The bias is knowable only when the 10:00 bar closes, which is 10:01.
        assert plan.bias_time == datetime(2026, 9, 14, 14, 1, tzinfo=UTC)

    def test_close_below_the_midline_is_a_short_with_the_fib_flipped(self) -> None:
        bars = [*_range_bars(), _bar(10, 0, 101, 104, 100, 103)]

        plan = find_plan(bars, NyFibRetestConfig())

        assert plan is not None
        assert plan.direction == SignalDirection.SELL
        assert plan.entry == pytest.approx(105.0)
        assert plan.stop == pytest.approx(107.64)   # above the entry, as a short's stop must be
        assert plan.target == pytest.approx(93.82)  # below the range, as a short's target must be
        assert plan.risk == pytest.approx(2.64)

    def test_close_exactly_on_the_midline_gives_no_side(self) -> None:
        bars = [*_range_bars(), _bar(10, 0, 101, 108, 101, 105)]

        assert find_plan(bars, NyFibRetestConfig()) is None

    def test_the_stop_is_always_behind_the_entry_and_the_target_beyond_the_range(self) -> None:
        for close, long in ((107, True), (103, False)):
            plan = find_plan([*_range_bars(), _bar(10, 0, 101, 108, 100, close)], NyFibRetestConfig())
            assert plan is not None
            sign = 1 if long else -1
            assert (plan.entry - plan.stop) * sign > 0
            assert (plan.target - plan.entry) * sign > 0
            assert (plan.target - (110.0 if long else 100.0)) * sign > 0


class TestRange:
    def test_bars_outside_the_window_do_not_widen_the_range(self) -> None:
        bars = [
            _bar(9, 29, 104, 500, 1, 104),   # pre-open spike: outside [09:30, 10:00)
            *_range_bars(),
            _bar(10, 0, 101, 400, 2, 107),   # the bias bar's own wick is not range either
        ]

        plan = find_plan(bars, NyFibRetestConfig())

        assert plan is not None
        assert (plan.range_high, plan.range_low) == (110.0, 100.0)

    def test_day_without_the_0930_bar_is_skipped(self) -> None:
        # A holiday or half day can open late; the later bars are a different window
        # wearing the same name, so the day has no setup rather than a narrower range.
        bars = [_bar(9, 45, 105, 110, 104, 108), _bar(9, 59, 108, 109, 100, 101),
                _bar(10, 0, 101, 108, 101, 107)]

        assert find_plan(bars, NyFibRetestConfig()) is None

    def test_day_without_the_bias_bar_is_skipped(self) -> None:
        # Falling back to an older bar would read the bias off a stale price.
        bars = [*_range_bars(), _bar(10, 5, 101, 108, 101, 107)]

        assert find_plan(bars, NyFibRetestConfig()) is None

    def test_flat_range_is_skipped(self) -> None:
        bars = [_bar(9, 30, 100, 100, 100, 100), _bar(10, 0, 100, 108, 100, 107)]

        assert find_plan(bars, NyFibRetestConfig()) is None


class TestConfig:
    def test_reward_is_fixed_by_the_three_levels_alone(self) -> None:
        assert NyFibRetestConfig().reward_r == pytest.approx((1.618 - 0.5) / (0.5 - 0.236))
        assert NyFibRetestConfig().reward_r == pytest.approx(4.2348, abs=1e-4)

    def test_a_target_inside_the_entry_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            NyFibRetestConfig(target_fib=0.4)

    def test_a_stop_beyond_the_entry_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            NyFibRetestConfig(stop_fib=0.6)

    def test_midline_is_the_same_point_whichever_way_the_fib_is_drawn(self) -> None:
        assert fib_level(110, 100, 0.5, long=True) == fib_level(110, 100, 0.5, long=False)


class TestOrderLifetime:
    def test_limit_is_cancelled_at_the_configured_ny_time(self) -> None:
        plan = find_plan([*_range_bars(), _bar(10, 0, 101, 108, 101, 107)], NyFibRetestConfig())
        assert plan is not None

        assert order_expires_at(plan, NyFibRetestConfig()) == datetime(2026, 9, 14, 20, 0, tzinfo=UTC)
        early = NyFibRetestConfig(order_expires=time(11, 0))
        assert order_expires_at(plan, early) == datetime(2026, 9, 14, 15, 0, tzinfo=UTC)


class TestSetupId:
    def test_tag_fits_the_twenty_characters_mt5_keeps(self) -> None:
        assert len(STRATEGY_TAG) <= 20

    def test_id_carries_the_date_and_side(self) -> None:
        plan = find_plan([*_range_bars(), _bar(10, 0, 101, 108, 101, 107)], NyFibRetestConfig())
        assert plan is not None

        assert setup_id("NDX100", plan) == f"{STRATEGY_TAG}_NDX100_20260914_BUY"
