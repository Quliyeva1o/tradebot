"""Unit tests for NasdaqMidlineSweepStrategy (NASDAQ Midline Sweep Strategy v2).

Uses a bare MarketState (append_bar/get_latest_bar/bars_view only), same
approach as test_accumulation_breakout.py, since this strategy reads only
raw M5 bars and keeps its own daily-scoped state.
"""

from datetime import UTC, datetime

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState
from strategy.diagnostics import RejectionReason
from strategy.nasdaq_midline_sweep import NasdaqMidlineSweepStrategy


def _bar(hour: int, minute: int, o: float, h: float, l: float, c: float, day: int = 5) -> Bar:
    return Bar(
        timestamp=datetime(2026, 1, day, hour, minute, tzinfo=UTC),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=100.0,
    )


def _new_state() -> MarketState:
    return MarketState(symbol="USTEC", timeframe=Timeframe.M5)


def _feed(state: MarketState, strategy: NasdaqMidlineSweepStrategy, bar: Bar):
    state.append_bar(bar)
    return strategy.evaluate(state)


# Build-session bars (09:30-09:45 UTC, session_timezone="UTC" in tests): closes
# average to exactly 100.0, each body a constant 0.5 (baseline for the SMA
# displacement filter). Session end bar (09:50) is deliberately neutral
# (close near mid) so it never itself produces a signal.
BUILD_SESSION_BARS = [
    _bar(9, 30, 100.0, 100.6, 99.9, 100.5),
    _bar(9, 35, 100.0, 100.1, 99.4, 99.5),
    _bar(9, 40, 100.0, 100.6, 99.9, 100.5),
    _bar(9, 45, 100.0, 100.1, 99.4, 99.5),
]
SESSION_END_BAR = _bar(9, 50, 100.0, 100.6, 99.9, 100.5)


def _feed_build_session(state: MarketState, strategy: NasdaqMidlineSweepStrategy) -> list:
    """Feeds the 4 build-session bars + the neutral session-end bar."""
    results = [_feed(state, strategy, bar) for bar in BUILD_SESSION_BARS]
    results.append(_feed(state, strategy, SESSION_END_BAR))
    return results


class TestMidlineCalculation:
    """Build-session accumulation and zone freeze."""

    def test_midline_and_zone_freeze_at_session_end(self) -> None:
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        results = _feed_build_session(state, strategy)

        # All 5 bars (4 build + session-end) reject: build-session bars are
        # rejected before the zone exists; the session-end bar is neutral.
        for r in results:
            assert r is None
        assert strategy.diagnostics.rejections[RejectionReason.ZONE_NOT_READY] == 4
        assert strategy.diagnostics.rejections[RejectionReason.WRONG_SIDE_OF_MID] == 1

        assert strategy._zone_ready is True
        assert strategy._mid == 100.0
        assert strategy._upper == 110.0
        assert strategy._lower == 90.0

    def test_zone_not_ready_before_session_end(self) -> None:
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        for bar in BUILD_SESSION_BARS:
            result = _feed(state, strategy, bar)
            assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.ZONE_NOT_READY] == 4
        assert strategy._zone_ready is False


class TestSweepLogic:
    """Sweep+reclaim, mid-buffer side filter, and displacement gating."""

    def test_long_sweep_and_displacement_generates_setup(self) -> None:
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        _feed_build_session(state, strategy)

        # low=108 < upper(110) -> sweep; close=112 > upper -> reclaim;
        # close=112 > mid+buffer(105) -> long_ok; body=7 >> avg body baseline.
        breakout_bar = _bar(10, 0, 105.0, 112.5, 108.0, 112.0)
        setup = _feed(state, strategy, breakout_bar)

        assert setup is not None
        assert setup.direction == SignalDirection.BUY
        assert setup.entry_zone == (112.0, 112.0)
        assert setup.stop_zone == (90.0, 90.0)  # sl = lower
        # risk = 112 - 90 = 22; reward = 22 * 2.0 (default rr) = 44
        assert setup.target_zone == (156.0, 156.0)
        assert strategy._trade_taken is True
        assert setup.strategy_name == "NasdaqMidlineSweepStrategy"

    def test_short_sweep_and_displacement_generates_setup(self) -> None:
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        _feed_build_session(state, strategy)

        # high=92 > lower(90) -> sweep; close=88 < lower -> reclaim;
        # close=88 < mid-buffer(95) -> short_ok; body=3 >> avg body baseline.
        breakout_bar = _bar(10, 0, 91.0, 92.0, 87.5, 88.0)
        setup = _feed(state, strategy, breakout_bar)

        assert setup is not None
        assert setup.direction == SignalDirection.SELL
        assert setup.entry_zone == (88.0, 88.0)
        assert setup.stop_zone == (110.0, 110.0)  # sl = upper
        assert setup.target_zone == (44.0, 44.0)

    def test_wrong_side_of_mid_rejection(self) -> None:
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        _feed_build_session(state, strategy)
        strategy.diagnostics.reset()

        # Close stays within mid +/- buffer (100 +/- 5).
        neutral_bar = _bar(10, 0, 100.0, 102.0, 98.0, 102.0)
        result = _feed(state, strategy, neutral_bar)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.WRONG_SIDE_OF_MID] == 1

    def test_no_sweep_rejection_when_no_dip_back_to_edge(self) -> None:
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        _feed_build_session(state, strategy)
        strategy.diagnostics.reset()

        # close beyond mid+buffer, but low (111.5) never dipped back to/below
        # upper (110) -- a gap with no sweep to reclaim.
        gap_bar = _bar(10, 0, 112.0, 113.5, 111.5, 113.0)
        result = _feed(state, strategy, gap_bar)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_SWEEP] == 1

    def test_no_displacement_rejection_when_body_too_small(self) -> None:
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        _feed_build_session(state, strategy)
        strategy.diagnostics.reset()

        # Sweep + mid-buffer conditions satisfied (low=108 < upper=110,
        # close=110.3 > upper and > mid+buffer=105), but body (0.6) is below
        # the 1.2x threshold against the shifted rolling avg body (0.52).
        small_body_bar = _bar(10, 0, 109.7, 110.4, 108.0, 110.3)
        result = _feed(state, strategy, small_body_bar)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_DISPLACEMENT] == 1


class TestOneTradePerDay:
    """tradeTaken guard: only one trade fires per day, reset at the next build session."""

    def test_trade_already_taken_blocks_further_signals_same_day(self) -> None:
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        _feed_build_session(state, strategy)

        breakout_bar = _bar(10, 0, 105.0, 112.5, 108.0, 112.0)
        setup = _feed(state, strategy, breakout_bar)
        assert setup is not None

        another_sweep = _bar(10, 5, 112.0, 113.0, 108.5, 112.5)
        result = _feed(state, strategy, another_sweep)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.TRADE_ALREADY_TAKEN] == 1

    def test_new_build_session_resets_state_and_allows_a_new_trade(self) -> None:
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        _feed_build_session(state, strategy)

        breakout_bar = _bar(10, 0, 105.0, 112.5, 108.0, 112.0)
        setup = _feed(state, strategy, breakout_bar)
        assert setup is not None
        assert strategy._trade_taken is True
        assert strategy._zone_ready is True

        # Next day's build session start must reset mid/zone/tradeTaken.
        next_day_first_bar = _bar(9, 30, 100.0, 100.6, 99.9, 100.5, day=6)
        result = _feed(state, strategy, next_day_first_bar)

        assert result is None
        assert strategy._trade_taken is False
        assert strategy._zone_ready is False
        assert strategy._count_close == 1
        assert strategy.diagnostics.rejections[RejectionReason.ZONE_NOT_READY] >= 1


class TestRecordTradeTaken:
    """Sprint 8: evaluate()'s record_trade_taken parameter and mark_trade_taken().

    Fixes a bug where check_signal()'s replay mechanism (live_signal_check.py)
    -- which calls evaluate() on historical bars solely to rebuild daily
    state, discarding whatever setup a replay bar's evaluation returns --
    would still let that discarded setup mark _trade_taken=True as a side
    effect. If the live "final bar" evaluation that would have reported
    that signal was ever skipped (e.g. a missed scheduler cycle), the day's
    one-trade allowance was permanently and silently consumed anyway,
    blocking a later, genuinely new and unrelated signal that same day.
    """

    def test_record_trade_taken_false_still_returns_the_setup(self) -> None:
        """A replay-mode call still generates a real setup.

        State-building and setup detection are unaffected -- only the
        trade_taken side effect is suppressed.
        """
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        _feed_build_session(state, strategy)

        breakout_bar = _bar(10, 0, 105.0, 112.5, 108.0, 112.0)
        state.append_bar(breakout_bar)
        setup = strategy.evaluate(state, record_trade_taken=False)

        assert setup is not None
        assert setup.direction == SignalDirection.BUY

    def test_record_trade_taken_false_does_not_set_the_flag(self) -> None:
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        _feed_build_session(state, strategy)

        breakout_bar = _bar(10, 0, 105.0, 112.5, 108.0, 112.0)
        state.append_bar(breakout_bar)
        strategy.evaluate(state, record_trade_taken=False)

        assert strategy._trade_taken is False

    def test_a_missed_replay_signal_does_not_block_a_later_genuine_signal_same_day(self) -> None:
        """The core regression scenario.

        A setup found ONLY during a replay pass must not consume the day's
        one-trade guard -- a later, independently-qualifying bar (evaluated
        normally, i.e. the "live final bar") must still be able to fire.
        """
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        _feed_build_session(state, strategy)

        missed_breakout = _bar(10, 0, 105.0, 112.5, 108.0, 112.0)
        state.append_bar(missed_breakout)
        missed_setup = strategy.evaluate(state, record_trade_taken=False)
        assert missed_setup is not None  # confirms it WOULD have been a valid signal
        assert strategy._trade_taken is False

        # A later bar, evaluated normally (the "live final bar" for a
        # subsequent run), independently qualifies as its own fresh setup.
        later_breakout = _bar(10, 5, 105.0, 112.5, 108.0, 112.0)
        setup = _feed(state, strategy, later_breakout)

        assert setup is not None
        assert setup.direction == SignalDirection.BUY

    def test_mark_trade_taken_blocks_further_signals_for_the_rest_of_the_day(self) -> None:
        """A real confirmed trade must still block the rest of the day.

        Once confirmed taken (the explicit method, not evaluate()'s
        automatic side effect), it must still correctly block further
        evaluation for the rest of the day -- Rule 2's CHECK is unaffected
        by record_trade_taken.
        """
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        _feed_build_session(state, strategy)
        assert strategy._trade_taken is False

        strategy.mark_trade_taken()

        another_sweep = _bar(10, 0, 105.0, 112.5, 108.0, 112.0)
        result = _feed(state, strategy, another_sweep)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.TRADE_ALREADY_TAKEN] == 1

    def test_default_evaluate_call_matches_old_unconditional_behavior(self) -> None:
        """No record_trade_taken kwarg at all must behave exactly as before.

        Every existing caller (backtest's StrategyEngine.run(), direct unit
        tests, a live final-bar evaluation) still sets the flag automatically.
        """
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        _feed_build_session(state, strategy)

        breakout_bar = _bar(10, 0, 105.0, 112.5, 108.0, 112.0)
        setup = _feed(state, strategy, breakout_bar)

        assert setup is not None
        assert strategy._trade_taken is True


class TestDiagnosticsAndConfig:
    """Diagnostics bookkeeping and config-overlay wiring."""

    def test_reset_clears_diagnostics_and_day_state(self) -> None:
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        _feed_build_session(state, strategy)
        assert strategy._zone_ready is True

        strategy.reset()
        assert strategy._zone_ready is False
        assert strategy._count_close == 0
        assert strategy._trade_taken is False
        assert strategy.diagnostics.evaluations == 0
        assert strategy.diagnostics.rejections == {}

    def test_config_overlay_applies_all_fields(self) -> None:
        from datetime import time

        from strategy.nasdaq_midline_sweep import NasdaqMidlineSweepConfig

        config = NasdaqMidlineSweepConfig(
            range_size=20.0,
            risk_reward=3.0,
            mid_buffer=8.0,
            body_multiplier=1.5,
            sma_period=10,
            build_session_start=time(8, 0),
            build_session_end=time(8, 20),
            session_timezone="UTC",
        )
        strategy = NasdaqMidlineSweepStrategy(config=config)
        assert strategy.range_size == 20.0
        assert strategy.risk_reward == 3.0
        assert strategy.mid_buffer == 8.0
        assert strategy.body_multiplier == 1.5
        assert strategy.sma_period == 10
        assert strategy.build_session_start == time(8, 0)
        assert strategy.build_session_end == time(8, 20)
        assert strategy.session_timezone == "UTC"

    def test_config_overlay_applies_day_session_end(self) -> None:
        from datetime import time

        from strategy.nasdaq_midline_sweep import NasdaqMidlineSweepConfig

        config = NasdaqMidlineSweepConfig(day_session_end=time(16, 0))
        strategy = NasdaqMidlineSweepStrategy(config=config)
        assert strategy.day_session_end == time(16, 0)


class TestBug50GapRobustness:
    """Regression test for Bug #50: the day-reset must be immune to a data gap
    that skips straight over an entire build session, not just a transition
    flag that only fires on an observed in-session -> out-of-session edge.
    """

    def test_gap_skipping_entire_build_session_still_resets_and_blocks_stale_trade(self) -> None:
        """Day 5: full build session + a breakout that takes a trade (_trade_taken=True,
        _zone_ready=True, mid=100.0). Then a gap skips ALL of day 6's build session
        (09:30-09:50) entirely -- the next bar fed is day 6 at 10:00, well after
        build_session_end.

        Before the Bug #50 fix, the day-reset was a transition flag
        (`in_build_session and not previously_in_build_session`). At the day-6 10:00
        bar, in_build_session is False (10:00 is outside 09:30-09:50) and the
        previous bar's in_build_session was ALSO False (day 5's post-breakout bar
        was also outside the window) -- the transition edge is never observed, so
        the reset never fires. Day 6 would silently inherit day 5's stale
        _trade_taken=True and stale zone, rejecting via TRADE_ALREADY_TAKEN using
        YESTERDAY's state instead of correctly recognizing day 6 has no zone yet.

        After the fix (calendar-date comparison), the day-6 10:00 bar is
        recognized as being at/after build_session_start on a NEW date regardless
        of the gap, so it resets immediately: zone/trade-taken state is correctly
        cleared, and since the gap also consumed day 6's entire build window (zero
        bars accumulated), the zone correctly stays NOT ready (there is no
        fabricated midline) rather than any stale carryover.
        """
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        _feed_build_session(state, strategy)

        day5_breakout = _bar(10, 0, 105.0, 112.5, 108.0, 112.0)
        setup = _feed(state, strategy, day5_breakout)
        assert setup is not None
        assert strategy._trade_taken is True
        assert strategy._zone_ready is True
        assert strategy._mid == 100.0

        # Gap: skip day 6's entire 09:30-09:50 build session. Next bar is day 6, 10:00.
        gap_bar_after_missed_session = _bar(10, 0, 100.0, 100.6, 99.9, 100.5, day=6)
        result = _feed(state, strategy, gap_bar_after_missed_session)

        assert result is None
        assert strategy._trade_taken is False  # correctly cleared, not stale from day 5
        assert strategy._zone_ready is False  # correctly NOT ready -- gap consumed the build window
        assert strategy._count_close == 0
        assert strategy.diagnostics.rejections[RejectionReason.ZONE_NOT_READY] >= 1
        # Must NOT be misreported as a stale same-day duplicate-trade block.
        assert RejectionReason.TRADE_ALREADY_TAKEN not in strategy.diagnostics.rejections

    def test_gap_landing_mid_build_session_still_resets_and_partially_accumulates(self) -> None:
        """A gap that skips only the FIRST part of day 6's build session (e.g. the
        09:30 and 09:35 bars are missing) must still reset for the new day and
        accumulate whatever build-session bars remain, rather than either failing
        to reset or discarding the partial accumulation.
        """
        state = _new_state()
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        _feed_build_session(state, strategy)
        day5_breakout = _bar(10, 0, 105.0, 112.5, 108.0, 112.0)
        _feed(state, strategy, day5_breakout)
        assert strategy._trade_taken is True

        # Day 6: gap skips 09:30/09:35, first bar seen is 09:40 (still inside the
        # build window), followed by the normal 09:45 bar and session-end bar.
        first_seen_bar = _bar(9, 40, 100.0, 100.6, 99.9, 100.5, day=6)
        result = _feed(state, strategy, first_seen_bar)

        assert result is None
        assert strategy._trade_taken is False  # reset fired despite the gap
        assert strategy._zone_ready is False
        assert strategy._count_close == 1  # the one build-session bar we did see

        _feed(state, strategy, _bar(9, 45, 100.0, 100.1, 99.4, 99.5, day=6))
        _feed(state, strategy, _bar(9, 50, 100.0, 100.6, 99.9, 100.5, day=6))

        assert strategy._zone_ready is True
        assert strategy._count_close == 2  # only the two bars actually observed


class TestRecommendedMaxHoldingBars:
    """recommended_max_holding_bars() must default to None (unlimited
    holding, identical to pre-existing behavior) unless the caller opts in
    via day_session_end -- no hardcoded wall-clock assumption.
    """

    def test_default_is_none_regardless_of_timeframe(self) -> None:
        strategy = NasdaqMidlineSweepStrategy()
        assert strategy.recommended_max_holding_bars(Timeframe.M5) is None
        assert strategy.recommended_max_holding_bars(Timeframe.M1) is None
        assert strategy.recommended_max_holding_bars(Timeframe.H1) is None

    def test_opt_in_computes_bars_for_classic_cash_session(self) -> None:
        from datetime import time

        strategy = NasdaqMidlineSweepStrategy(day_session_end=time(16, 0))
        # build_session_start 09:30 -> day_session_end 16:00 = 390 minutes.
        assert strategy.recommended_max_holding_bars(Timeframe.M5) == 78

    def test_opt_in_computes_bars_for_near_continuous_cfd_session(self) -> None:
        from datetime import time

        strategy = NasdaqMidlineSweepStrategy(day_session_end=time(23, 0))
        # build_session_start 09:30 -> day_session_end 23:00 = 810 minutes.
        assert strategy.recommended_max_holding_bars(Timeframe.M5) == 162
