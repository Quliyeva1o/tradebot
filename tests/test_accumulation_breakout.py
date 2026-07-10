"""Unit tests for AccumulationBreakoutStrategy (5 Candle Accumulation Breakout Retest).

Uses a bare MarketState (append_bar/get_latest_bar/bars_view only) rather than
the full MarketStateBuilder pipeline, since this strategy reads only raw bars
and keeps its own session-scoped state -- it has no dependency on swings,
structure, or SMC state.
"""

from datetime import datetime, timezone

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState
from strategy.accumulation_breakout import AccumulationBreakoutStrategy
from strategy.diagnostics import RejectionReason


def _bar(hour: int, minute: int, o: float, h: float, l: float, c: float, volume: float = 100.0, day: int = 5) -> Bar:
    return Bar(
        timestamp=datetime(2026, 1, day, hour, minute, tzinfo=timezone.utc),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=volume,
    )


def _new_state() -> MarketState:
    return MarketState(symbol="EURUSD", timeframe=Timeframe.M15)


def _feed(state: MarketState, strategy: AccumulationBreakoutStrategy, bar: Bar):
    state.append_bar(bar)
    return strategy.evaluate(state)


# The 5 accumulation bars shared by most tests below: each candle's close
# stays within the previous candle's high/low (Pine's validCandle), body is a
# constant 0.0005, volume a constant 100.0. Final range: high=1.1012, low=1.0995.
ACCUMULATION_BARS = [
    _bar(9, 30, 1.1000, 1.1010, 1.0995, 1.1005),
    _bar(9, 45, 1.1005, 1.1012, 1.0998, 1.1000),
    _bar(10, 0, 1.1000, 1.1011, 1.1000, 1.1005),
    _bar(10, 15, 1.1005, 1.1010, 1.1001, 1.1000),
    _bar(10, 30, 1.1000, 1.1009, 1.1002, 1.1005),
]


def _feed_accumulation(state: MarketState, strategy: AccumulationBreakoutStrategy) -> list:
    """Feeds the 5 shared accumulation bars, returns the list of evaluate() results."""
    return [_feed(state, strategy, bar) for bar in ACCUMULATION_BARS]


class TestAccumulationDetection:
    """5-candle accumulation range building (validCandle chain)."""

    def test_five_valid_candles_arm_the_range(self) -> None:
        state = _new_state()
        strategy = AccumulationBreakoutStrategy(sma_period=5, session_timezone="UTC")
        results = _feed_accumulation(state, strategy)

        # Bars 1-4: range not yet ready.
        for r in results[:4]:
            assert r is None
        assert strategy.diagnostics.rejections[RejectionReason.ACCUMULATION_NOT_READY] == 4

        # After bar 5: range is armed at [1.0995, 1.1012].
        assert strategy._range_ready is True
        assert strategy._count == 5
        assert strategy._range_high == 1.1012
        assert strategy._range_low == 1.0995

    def test_containment_break_restarts_the_run(self) -> None:
        state = _new_state()
        strategy = AccumulationBreakoutStrategy(sma_period=5, session_timezone="UTC")

        _feed(state, strategy, ACCUMULATION_BARS[0])
        _feed(state, strategy, ACCUMULATION_BARS[1])
        assert strategy._count == 2

        # A candle closing outside the previous candle's [low, high] breaks
        # containment and must restart the run from itself (count -> 1).
        breaking_bar = _bar(10, 0, 1.1000, 1.1050, 1.0950, 1.1040)  # close=1.1040 > prev high 1.1012
        _feed(state, strategy, breaking_bar)
        assert strategy._count == 1
        assert strategy._range_high == 1.1050
        assert strategy._range_low == 1.0950

    def test_no_setup_outside_session(self) -> None:
        """Bars entirely outside the session window never arm the range."""
        state = _new_state()
        strategy = AccumulationBreakoutStrategy(sma_period=5, session_timezone="UTC")

        outside_bars = [
            _bar(12, 0, 1.1000, 1.1010, 1.0995, 1.1005),
            _bar(12, 15, 1.1005, 1.1012, 1.0998, 1.1000),
            _bar(12, 30, 1.1000, 1.1011, 1.1000, 1.1005),
            _bar(12, 45, 1.1005, 1.1010, 1.1001, 1.1000),
            _bar(13, 0, 1.1000, 1.1009, 1.1002, 1.1005),
        ]
        results = [_feed(state, strategy, bar) for bar in outside_bars]

        assert all(r is None for r in results)
        assert strategy.diagnostics.rejections[RejectionReason.NOT_IN_SESSION] == 5
        assert strategy._range_ready is False
        assert strategy._count == 0


class TestBreakoutAndRetest:
    """Breakout (body + volume) and retest confirmation logic."""

    def test_breakout_and_immediate_retest_generates_setup(self) -> None:
        state = _new_state()
        strategy = AccumulationBreakoutStrategy(sma_period=5, session_timezone="UTC")
        _feed_accumulation(state, strategy)

        # Strong bullish candle: body=0.0025 (>1.3x avg body 0.0005), volume=200
        # (avg volume over last 5 = (100*4+200)/5=120, 200 > 1.5*120=180), close
        # beyond range_high (1.1012), low still <= range_high -> same-bar retest.
        breakout_bar = _bar(10, 45, 1.1005, 1.1032, 1.1004, 1.1030, volume=200.0)
        setup = _feed(state, strategy, breakout_bar)

        assert setup is not None
        assert setup.direction == SignalDirection.BUY
        assert setup.entry_zone == (1.1030, 1.1030)
        assert setup.stop_zone == (1.0995, 1.0995)
        # risk = 1.1030 - 1.0995 = 0.0035; reward = 0.0035 * 2.0 (default rr)
        assert setup.target_zone[0] == round(1.1030 + 0.0035 * 2.0, 5)
        assert strategy._trade_taken is True
        assert strategy.diagnostics.setups_generated == 1
        assert setup.strategy_name == "AccumulationBreakoutStrategy"

    def test_breakout_persists_until_later_retest(self) -> None:
        """Breakout flag is sticky: retest can confirm on a later bar, and is
        not gated by the session window (mirrors the Pine script, where only
        the accumulation-building step -- not the breakout/retest check -- is
        wrapped in `if inSession`)."""
        state = _new_state()
        strategy = AccumulationBreakoutStrategy(sma_period=5, session_timezone="UTC")
        _feed_accumulation(state, strategy)

        # Gap-up breakout: low (1.1020) is already above range_high (1.1012),
        # so no same-bar retest is possible yet.
        gap_breakout = _bar(10, 45, 1.1020, 1.1037, 1.1020, 1.1035, volume=200.0)
        result1 = _feed(state, strategy, gap_breakout)
        assert result1 is None
        assert strategy._breakout_up is True
        assert strategy.diagnostics.rejections[RejectionReason.NO_RETEST] == 1

        # Bar exactly at session_end (11:00, exclusive -> out of session) dips
        # back to touch range_high while still closing beyond it: retest.
        retest_bar = _bar(11, 0, 1.1035, 1.1036, 1.1010, 1.1015, volume=100.0)
        setup = _feed(state, strategy, retest_bar)

        assert setup is not None
        assert setup.direction == SignalDirection.BUY
        assert setup.entry_zone == (1.1015, 1.1015)

    def test_no_volume_spike_rejection(self) -> None:
        state = _new_state()
        strategy = AccumulationBreakoutStrategy(
            sma_period=5, require_volume_filter=True, session_timezone="UTC"
        )
        _feed_accumulation(state, strategy)

        # Strong body and price beyond range, but volume stays at baseline (100).
        weak_volume_breakout = _bar(10, 45, 1.1005, 1.1032, 1.1004, 1.1030, volume=100.0)
        result = _feed(state, strategy, weak_volume_breakout)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_VOLUME_SPIKE] == 1
        assert strategy._breakout_up is False

    def test_volume_filter_disabled_ignores_low_volume(self) -> None:
        state = _new_state()
        strategy = AccumulationBreakoutStrategy(
            sma_period=5, require_volume_filter=False, session_timezone="UTC"
        )
        _feed_accumulation(state, strategy)

        weak_volume_breakout = _bar(10, 45, 1.1005, 1.1032, 1.1004, 1.1030, volume=100.0)
        setup = _feed(state, strategy, weak_volume_breakout)

        assert setup is not None
        assert setup.direction == SignalDirection.BUY

    def test_no_breakout_rejection_when_body_too_small(self) -> None:
        state = _new_state()
        strategy = AccumulationBreakoutStrategy(sma_period=5, session_timezone="UTC")
        _feed_accumulation(state, strategy)
        # Bar 5 itself already falls through to the breakout check (range_ready
        # becomes true and is re-evaluated the same bar) and records its own
        # NO_BREAKOUT rejection; reset counters so this test isolates the one
        # under evaluation below.
        strategy.diagnostics.reset()

        # Closes beyond range_high, but body (0.0004) is below the 1.3x avg
        # body (0.00065) threshold -- not a valid displacement candle.
        small_body_bar = _bar(10, 45, 1.1013, 1.1020, 1.1010, 1.1017, volume=200.0)
        result = _feed(state, strategy, small_body_bar)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_BREAKOUT] == 1
        assert strategy._breakout_up is False


class TestOneTradePerSession:
    """tradeTaken guard: only one trade fires per session, reset at the next one."""

    def test_trade_already_taken_blocks_further_signals_same_session(self) -> None:
        state = _new_state()
        strategy = AccumulationBreakoutStrategy(sma_period=5, session_timezone="UTC")
        _feed_accumulation(state, strategy)

        breakout_bar = _bar(10, 45, 1.1005, 1.1032, 1.1004, 1.1030, volume=200.0)
        setup = _feed(state, strategy, breakout_bar)
        assert setup is not None

        # A later bar that would otherwise re-trigger a retest-like condition
        # must now be blocked by the tradeTaken guard (persists regardless of
        # session membership until the next session's reset).
        another_dip = _bar(11, 0, 1.1030, 1.1040, 1.1005, 1.1035, volume=200.0, day=5)
        result = _feed(state, strategy, another_dip)
        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.TRADE_ALREADY_TAKEN] == 1

    def test_new_session_resets_state_and_allows_a_new_trade(self) -> None:
        state = _new_state()
        strategy = AccumulationBreakoutStrategy(sma_period=5, session_timezone="UTC")
        _feed_accumulation(state, strategy)

        breakout_bar = _bar(10, 45, 1.1005, 1.1032, 1.1004, 1.1030, volume=200.0)
        setup = _feed(state, strategy, breakout_bar)
        assert setup is not None
        assert strategy._trade_taken is True

        # Out-of-session bar so _was_in_session becomes False before next day.
        _feed(state, strategy, _bar(11, 0, 1.1030, 1.1031, 1.1029, 1.1030, volume=100.0))
        assert strategy._was_in_session is False

        # Next day's session start must reset count/range/breakout/tradeTaken.
        next_day_bar = _bar(9, 30, 1.1030, 1.1040, 1.1025, 1.1035, volume=100.0, day=6)
        result = _feed(state, strategy, next_day_bar)

        assert result is None  # first bar of a fresh accumulation run
        assert strategy._trade_taken is False
        assert strategy._range_ready is False
        assert strategy._count == 1
        assert strategy.diagnostics.rejections[RejectionReason.ACCUMULATION_NOT_READY] >= 1


class TestDiagnosticsAndConfig:
    """Diagnostics bookkeeping and config-overlay wiring."""

    def test_reset_clears_diagnostics_and_session_state(self) -> None:
        state = _new_state()
        strategy = AccumulationBreakoutStrategy(sma_period=5, session_timezone="UTC")
        _feed_accumulation(state, strategy)
        assert strategy._range_ready is True

        strategy.reset()
        assert strategy._range_ready is False
        assert strategy._count == 0
        assert strategy._trade_taken is False
        assert strategy.diagnostics.evaluations == 0
        assert strategy.diagnostics.rejections == {}

    def test_config_overlay_applies_all_fields(self) -> None:
        from datetime import time

        from strategy.accumulation_breakout import AccumulationBreakoutConfig

        config = AccumulationBreakoutConfig(
            risk_reward=3.0,
            volume_multiplier=2.0,
            require_volume_filter=False,
            session_start=time(8, 0),
            session_end=time(9, 0),
            body_multiplier=1.5,
            accumulation_bars=4,
            sma_period=10,
        )
        strategy = AccumulationBreakoutStrategy(config=config)
        assert strategy.risk_reward == 3.0
        assert strategy.volume_multiplier == 2.0
        assert strategy.require_volume_filter is False
        assert strategy.session_start == time(8, 0)
        assert strategy.session_end == time(9, 0)
        assert strategy.body_multiplier == 1.5
        assert strategy.accumulation_bars == 4
        assert strategy.sma_period == 10


class TestDSTAwareSession:
    """The default NY session (09:30-11:00 local) must map to different UTC
    clock windows depending on whether the bar falls in EDT or EST, since
    Bar.timestamp is UTC but the Pine script's session is exchange-local."""

    def test_ny_session_boundary_shifts_between_winter_and_summer(self) -> None:
        strategy = AccumulationBreakoutStrategy()  # default session_timezone="America/New_York"

        # January (EST, UTC-5): 09:30-11:00 NY == 14:30-16:00 UTC.
        assert strategy._in_session(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)) is True
        assert strategy._in_session(datetime(2026, 1, 5, 14, 15, tzinfo=timezone.utc)) is False
        assert strategy._in_session(datetime(2026, 1, 5, 15, 59, tzinfo=timezone.utc)) is True
        assert strategy._in_session(datetime(2026, 1, 5, 16, 0, tzinfo=timezone.utc)) is False

        # July (EDT, UTC-4): 09:30-11:00 NY == 13:30-15:00 UTC -- one hour
        # earlier in UTC than the January window above.
        assert strategy._in_session(datetime(2026, 7, 5, 13, 30, tzinfo=timezone.utc)) is True
        assert strategy._in_session(datetime(2026, 7, 5, 13, 15, tzinfo=timezone.utc)) is False
        assert strategy._in_session(datetime(2026, 7, 5, 14, 59, tzinfo=timezone.utc)) is True
        assert strategy._in_session(datetime(2026, 7, 5, 15, 0, tzinfo=timezone.utc)) is False

    def test_same_utc_instant_differs_across_the_dst_boundary(self) -> None:
        """The identical UTC clock time (14:15) is outside the NY session in
        January (09:15 EST, before 09:30 open) but inside it in July (10:15
        EDT, within 09:30-11:00) -- proving the session window is computed
        per-bar-date via zoneinfo, not a single fixed UTC offset."""
        strategy = AccumulationBreakoutStrategy()

        assert strategy._in_session(datetime(2026, 1, 5, 14, 15, tzinfo=timezone.utc)) is False
        assert strategy._in_session(datetime(2026, 7, 5, 14, 15, tzinfo=timezone.utc)) is True

    def test_utc_session_timezone_bypasses_dst_conversion(self) -> None:
        """session_timezone='UTC' (used by the other tests in this file)
        treats session_start/session_end as literal UTC clock time, with no
        DST shift -- the January/July boundary difference above does not
        apply."""
        strategy = AccumulationBreakoutStrategy(session_timezone="UTC")

        assert strategy._in_session(datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc)) is True
        assert strategy._in_session(datetime(2026, 7, 5, 9, 30, tzinfo=timezone.utc)) is True
        assert strategy._in_session(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)) is False
        assert strategy._in_session(datetime(2026, 7, 5, 14, 30, tzinfo=timezone.utc)) is False
