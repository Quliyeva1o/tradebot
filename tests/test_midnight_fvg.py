"""Unit tests for MidnightFvgStrategy.

Uses a bare MarketState (append_bar/get_latest_bar only), like
tests/test_accumulation_breakout.py, since this strategy reads only raw
bars and keeps its own day-scoped state. Bars are timestamped directly in
America/New_York so NY-session-time assertions don't need manual UTC-offset
arithmetic (evaluate() converts via .astimezone(NY) regardless of the
timestamp's original tzinfo, so this is equivalent to real UTC-timestamped
MT5 data for every check this strategy performs).
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState
from strategy.diagnostics import RejectionReason
from strategy.midnight_fvg import MidnightFvgConfig, MidnightFvgStrategy

NY = ZoneInfo("America/New_York")


def _bar(day: int, hour: int, minute: int, o: float, h: float, l: float, c: float, month: int = 1) -> Bar:
    return Bar(
        timestamp=datetime(2026, month, day, hour, minute, tzinfo=NY),
        open=o, high=h, low=l, close=c, volume=100.0,
    )


def _new_state() -> MarketState:
    return MarketState(symbol="USTEC", timeframe=Timeframe.M1)


def _feed(state: MarketState, strategy: MidnightFvgStrategy, bar: Bar):
    state.append_bar(bar)
    return strategy.evaluate(state)


# A same-day bullish FVG: bar0 (00:00), bar1/middle (00:01), bar2/end (00:02).
# gap = bar2.low(103.6) - bar0.high(100.5) = 3.1 >= MIN_GAP_POINTS(3.0).
BULL_BAR0 = _bar(6, 0, 0, 100.0, 100.5, 99.8, 100.2)  # low=99.8 -> the SL (candle before the displacement candle)
BULL_BAR1_MIDDLE = _bar(6, 0, 1, 100.2, 101.0, 98.0, 99.0)  # displacement candle (no longer the SL source)
BULL_BAR2_END = _bar(6, 0, 2, 103.6, 104.0, 103.6, 103.9)  # low=103.6 -> the entry (upper edge)
BULL_BAR3_NO_TOUCH = _bar(6, 0, 3, 104.0, 106.0, 103.8, 105.0)  # low=103.8, does NOT touch 103.6
BULL_BAR4_TOUCH = _bar(6, 0, 4, 105.0, 105.5, 103.0, 103.5)  # low=103.0 <= 103.6 -> touch


class TestBullishFvgDetectAndEnter:
    def test_no_setup_until_third_candle(self) -> None:
        state = _new_state()
        strategy = MidnightFvgStrategy()
        assert _feed(state, strategy, BULL_BAR0) is None
        assert _feed(state, strategy, BULL_BAR1_MIDDLE) is None
        assert strategy._fvg_found is False

    def test_fvg_forms_but_does_not_self_touch_on_its_own_end_candle(self) -> None:
        """Regression test: the FVG's end/third candle must NOT also count as
        the retest -- its own wick trivially equals the near edge (upper ==
        bar2.low for a bullish gap), which would otherwise fire an instant
        false "retest" the moment the gap forms (see strategy/midnight_fvg.py
        STEP 1's inline comment).
        """
        state = _new_state()
        strategy = MidnightFvgStrategy()
        _feed(state, strategy, BULL_BAR0)
        _feed(state, strategy, BULL_BAR1_MIDDLE)
        result = _feed(state, strategy, BULL_BAR2_END)

        assert result is None
        assert strategy._fvg_found is True
        assert strategy._fvg_direction == SignalDirection.BUY
        assert strategy._fvg_upper == pytest.approx(103.6)
        assert strategy._fvg_lower == pytest.approx(100.5)
        assert strategy._fvg_sl_bar is BULL_BAR0
        assert strategy.diagnostics.rejections[RejectionReason.NO_RETEST] == 1

    def test_no_touch_bar_is_rejected_then_touch_bar_enters(self) -> None:
        state = _new_state()
        strategy = MidnightFvgStrategy()
        for bar in (BULL_BAR0, BULL_BAR1_MIDDLE, BULL_BAR2_END):
            _feed(state, strategy, bar)

        assert _feed(state, strategy, BULL_BAR3_NO_TOUCH) is None

        setup = _feed(state, strategy, BULL_BAR4_TOUCH)
        assert setup is not None
        assert setup.direction == SignalDirection.BUY
        assert setup.entry_zone == (103.6, 103.6)
        assert setup.stop_zone == (99.8, 99.8)
        risk = 103.6 - 99.8
        assert setup.target_zone == (pytest.approx(103.6 + 2.5 * risk), pytest.approx(103.6 + 2.5 * risk))
        assert setup.timestamp == BULL_BAR4_TOUCH.timestamp
        assert strategy._trade_taken is True

    def test_trade_already_taken_blocks_further_setups_same_day(self) -> None:
        state = _new_state()
        strategy = MidnightFvgStrategy()
        for bar in (BULL_BAR0, BULL_BAR1_MIDDLE, BULL_BAR2_END, BULL_BAR3_NO_TOUCH, BULL_BAR4_TOUCH):
            _feed(state, strategy, bar)

        # A second, independent bullish gap later the same session -- must be ignored.
        later0 = _bar(6, 0, 10, 110.0, 110.5, 109.8, 110.2)
        later1 = _bar(6, 0, 11, 110.2, 111.0, 109.5, 110.5)
        later2 = _bar(6, 0, 12, 114.0, 114.5, 114.0, 114.3)
        for bar in (later0, later1, later2):
            result = _feed(state, strategy, bar)
            assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.TRADE_ALREADY_TAKEN] == 3


class TestBearishFvg:
    def test_bearish_gap_symmetric_rules(self) -> None:
        # bar0.low(100.5) - bar2.high(97.4) = 3.1 >= 3.0 -> BEARISH.
        # upper_price = bar0.low = 100.5 (irrelevant to entry), lower_price = bar2.high = 97.4 (entry).
        bar0 = _bar(6, 0, 0, 100.0, 100.2, 100.5, 100.1)  # high=100.2 -> the SL (candle before the displacement candle)
        bar1_middle = _bar(6, 0, 1, 99.8, 101.5, 99.0, 100.0)  # displacement candle (no longer the SL source)
        bar2_end = _bar(6, 0, 2, 97.4, 97.4, 97.0, 97.2)  # high=97.4 -> the entry (near edge)
        no_touch = _bar(6, 0, 3, 97.0, 97.3, 96.5, 96.8)  # high=97.3, does not reach 97.4
        touch = _bar(6, 0, 4, 96.8, 97.6, 96.5, 97.5)  # high=97.6 >= 97.4 -> touch

        state = _new_state()
        strategy = MidnightFvgStrategy()
        for bar in (bar0, bar1_middle, bar2_end):
            _feed(state, strategy, bar)
        assert strategy._fvg_direction == SignalDirection.SELL
        assert strategy._fvg_lower == pytest.approx(97.4)

        assert _feed(state, strategy, no_touch) is None
        setup = _feed(state, strategy, touch)
        assert setup is not None
        assert setup.direction == SignalDirection.SELL
        assert setup.entry_zone == (97.4, 97.4)
        assert setup.stop_zone == (100.2, 100.2)
        risk = 100.2 - 97.4
        assert setup.target_zone == (pytest.approx(97.4 - 2.5 * risk), pytest.approx(97.4 - 2.5 * risk))


class TestNoFvgAndDayRollover:
    def test_gap_below_min_points_is_ignored(self) -> None:
        # gap = 102.0 - 100.5 = 1.5 < default MIN_GAP_POINTS(3.0).
        bar0 = _bar(6, 0, 0, 100.0, 100.5, 99.8, 100.2)
        bar1 = _bar(6, 0, 1, 100.2, 101.0, 98.0, 99.0)
        bar2 = _bar(6, 0, 2, 102.0, 102.4, 102.0, 102.2)

        state = _new_state()
        strategy = MidnightFvgStrategy()
        for bar in (bar0, bar1, bar2):
            result = _feed(state, strategy, bar)
        assert result is None
        assert strategy._fvg_found is False
        # All 3 feeds reject NO_MATCHING_FVG: bar0/bar1 because the buffer
        # has < 3 bars yet, bar2 because the gap itself is too small.
        assert strategy.diagnostics.rejections[RejectionReason.NO_MATCHING_FVG] == 3

    def test_no_fvg_in_session_rejects_for_rest_of_day(self) -> None:
        state = _new_state()
        strategy = MidnightFvgStrategy()
        flat_bars = [_bar(6, 0, m, 100.0, 100.1, 99.9, 100.0) for m in range(31)]  # 00:00-00:30, no gaps
        for bar in flat_bars:
            assert _feed(state, strategy, bar) is None
        after_session = _bar(6, 5, 0, 100.0, 100.1, 99.9, 100.0)
        assert _feed(state, strategy, after_session) is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_MATCHING_FVG] >= 2

    def test_day_rollover_resets_state(self) -> None:
        state = _new_state()
        strategy = MidnightFvgStrategy()
        for bar in (BULL_BAR0, BULL_BAR1_MIDDLE, BULL_BAR2_END, BULL_BAR3_NO_TOUCH, BULL_BAR4_TOUCH):
            _feed(state, strategy, bar)
        assert strategy._trade_taken is True

        next_day_bar0 = _bar(7, 0, 0, 200.0, 200.1, 199.9, 200.0)
        _feed(state, strategy, next_day_bar0)
        assert strategy._trade_taken is False
        assert strategy._fvg_found is False
        assert strategy._current_date == next_day_bar0.timestamp.date()

    def test_reset_clears_diagnostics_and_state(self) -> None:
        state = _new_state()
        strategy = MidnightFvgStrategy()
        for bar in (BULL_BAR0, BULL_BAR1_MIDDLE, BULL_BAR2_END, BULL_BAR3_NO_TOUCH, BULL_BAR4_TOUCH):
            _feed(state, strategy, bar)

        strategy.reset()
        assert strategy.diagnostics.evaluations == 0
        assert strategy.diagnostics.setups_generated == 0
        assert strategy._trade_taken is False
        assert strategy._current_date is None


class TestCrossMidnightTail:
    def test_fvg_middle_candle_at_00_00_uses_previous_days_23_59_bar(self) -> None:
        """A 3-candle FVG whose middle candle is the session's very first bar
        (00:00) needs the previous day's 23:59 bar as its first/bar0 --
        see strategy/midnight_fvg.py's "Cross-midnight FVG edge case".
        """
        prev_day_tail = _bar(5, 23, 59, 100.0, 100.5, 99.8, 100.2)  # bar0 (SL source, low=99.8)
        midnight_middle = _bar(6, 0, 0, 100.2, 101.0, 98.0, 99.0)  # bar1 (displacement candle)
        next_minute_end = _bar(6, 0, 1, 103.6, 104.0, 103.6, 103.9)  # bar2 (entry source, low=103.6)
        touch = _bar(6, 0, 5, 105.0, 105.5, 103.0, 103.5)

        state = _new_state()
        strategy = MidnightFvgStrategy()
        assert _feed(state, strategy, prev_day_tail) is None
        assert strategy._fvg_found is False  # not in session (23:59), only recorded to the trailing tail

        assert _feed(state, strategy, midnight_middle) is None  # only 2 bars (tail + this) so far
        result = _feed(state, strategy, next_minute_end)  # 3rd bar completes the gap
        assert result is None  # same-tick self-touch guard, see TestBullishFvgDetectAndEnter
        assert strategy._fvg_found is True
        assert strategy._fvg_sl_bar is prev_day_tail
        assert strategy._fvg_upper == pytest.approx(103.6)

        setup = _feed(state, strategy, touch)
        assert setup is not None
        assert setup.stop_zone == (99.8, 99.8)

    def test_stale_tail_bars_far_from_midnight_are_not_seeded(self) -> None:
        """Regression test: after a data gap, the last-seen bars might not be
        23:58/23:59 -- e.g. a session ending at 00:03/00:04 the prior day (as
        crafted here). Seeding those into the new day's detection buffer
        could otherwise let their own coincidental price jump register as a
        bogus FVG whose "middle candle" time-of-day still falls inside
        [00:00, 00:30) even though it belongs to yesterday.
        """
        stale_a = _bar(6, 0, 3, 104.0, 106.0, 103.8, 105.0)
        stale_b = _bar(6, 0, 4, 105.0, 105.5, 103.0, 103.5)
        next_day_bar0 = _bar(7, 0, 0, 200.0, 200.1, 199.9, 200.0)

        state = _new_state()
        strategy = MidnightFvgStrategy()
        _feed(state, strategy, stale_a)
        _feed(state, strategy, stale_b)

        result = _feed(state, strategy, next_day_bar0)
        assert result is None
        assert strategy._fvg_found is False
        assert strategy._session_bars == [next_day_bar0]


class TestConfigValidation:
    def test_invalid_entry_mode_rejected(self) -> None:
        with pytest.raises(ValueError):
            MidnightFvgConfig(entry_mode="bogus")

    def test_session_start_must_precede_session_end(self) -> None:
        from datetime import time
        with pytest.raises(ValueError):
            MidnightFvgConfig(session_start=time(0, 30), session_end=time(0, 0))

    def test_non_positive_fixed_tp_r_rejected(self) -> None:
        with pytest.raises(ValueError):
            MidnightFvgConfig(fixed_tp_r=0.0)


class TestRetestWindowCandlesCap:
    def test_capped_window_abandons_setup_if_not_retested_in_time(self) -> None:
        state = _new_state()
        strategy = MidnightFvgStrategy(config=MidnightFvgConfig(retest_window_candles=1))
        for bar in (BULL_BAR0, BULL_BAR1_MIDDLE, BULL_BAR2_END):
            _feed(state, strategy, bar)  # formation tick: no retest check yet (see STEP 1 guard)

        # bars_since_fvg becomes 1 here (<=1, still allowed)...
        assert _feed(state, strategy, BULL_BAR3_NO_TOUCH) is None
        # ...bars_since_fvg becomes 2 here (>1, cap exceeded) -- rejected even
        # though BULL_BAR4_TOUCH's low would otherwise satisfy the touch.
        result = _feed(state, strategy, BULL_BAR4_TOUCH)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_RETEST] >= 2
