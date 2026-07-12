"""Unit tests for OpeningRangeBreakoutStrategy (Strategy #3, plain-text spec).

Uses a bare MarketState (append_bar/get_latest_bar/bars_view only), same
approach as the other ported strategies' tests, since this strategy reads
only raw M1 bars and keeps its own daily-scoped state.
"""

from datetime import UTC, datetime

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState
from strategy.diagnostics import RejectionReason
from strategy.opening_range_breakout import OpeningRangeBreakoutStrategy


def _bar(hour: int, minute: int, o: float, h: float, l: float, c: float, volume: float = 100.0, day: int = 5) -> Bar:
    return Bar(
        timestamp=datetime(2026, 1, day, hour, minute, tzinfo=UTC),
        open=o,
        high=h,
        low=l,
        close=c,
        volume=volume,
    )


def _new_state() -> MarketState:
    return MarketState(symbol="USTEC", timeframe=Timeframe.M1)


def _feed(state: MarketState, strategy: OpeningRangeBreakoutStrategy, bar: Bar):
    state.append_bar(bar)
    return strategy.evaluate(state)


# First 5 M1 bars (09:30-09:34 UTC, session_timezone="UTC" in tests): the
# first bar deliberately has a NARROWER high/low than the eventual range
# extremes, so the "SL anchors to the first bar, not the range" design
# choice is distinguishable in tests. All volumes are a constant 100.0
# baseline for the volume-spike filter.
OPENING_RANGE_BARS = [
    _bar(9, 30, 100.0, 100.2, 99.8, 100.1),  # first bar: high=100.2, low=99.8
    _bar(9, 31, 100.1, 100.8, 99.9, 100.3),  # widens range high to 100.8
    _bar(9, 32, 100.3, 100.5, 99.2, 100.0),  # widens range low to 99.2
    _bar(9, 33, 100.0, 100.6, 99.6, 100.4),
    _bar(9, 34, 100.1, 100.4, 99.9, 100.2),  # closes inside range; falls through to breakout check
]


def _feed_opening_range(state: MarketState, strategy: OpeningRangeBreakoutStrategy) -> list:
    return [_feed(state, strategy, bar) for bar in OPENING_RANGE_BARS]


class TestRangeCalculation:
    """Opening-range accumulation from the first range_bars candles."""

    def test_range_and_first_bar_anchors_after_five_bars(self) -> None:
        state = _new_state()
        strategy = OpeningRangeBreakoutStrategy(volume_lookback=5, session_timezone="UTC")
        results = _feed_opening_range(state, strategy)

        for r in results:
            assert r is None
        # Bars 1-4: range not yet ready. Bar 5 itself completes the range
        # (range_ready becomes true) and falls through to the breakout check
        # the same bar (mirrors AccumulationBreakoutStrategy's same pattern);
        # its close stays inside the range, so it records its own NO_BREAKOUT.
        assert strategy.diagnostics.rejections[RejectionReason.RANGE_NOT_READY] == 4
        assert strategy.diagnostics.rejections[RejectionReason.NO_BREAKOUT] == 1

        assert strategy._range_ready is True
        assert strategy._range_high == 100.8
        assert strategy._range_low == 99.2
        # First-bar anchors are distinct from the range extremes.
        assert strategy._first_bar_high == 100.2
        assert strategy._first_bar_low == 99.8

    def test_range_not_ready_before_five_bars(self) -> None:
        state = _new_state()
        strategy = OpeningRangeBreakoutStrategy(volume_lookback=5, session_timezone="UTC")
        for bar in OPENING_RANGE_BARS[:4]:
            assert _feed(state, strategy, bar) is None
        assert strategy._range_ready is False
        assert strategy._range_count == 4


class TestBreakoutAndVolume:
    """Breakout + close-side confirmation + volume-spike gating."""

    def test_bullish_breakout_with_volume_generates_setup_sl_at_first_bar(self) -> None:
        state = _new_state()
        strategy = OpeningRangeBreakoutStrategy(volume_lookback=5, session_timezone="UTC")
        _feed_opening_range(state, strategy)

        # close=101.0 > range_high(100.8); high=101.1 > range_high (attempted);
        # avg volume over last 5 = (100*4+300)/5=140, 300 > 1.5*140=210.
        breakout_bar = _bar(9, 35, 100.5, 101.1, 100.4, 101.0, volume=300.0)
        setup = _feed(state, strategy, breakout_bar)

        assert setup is not None
        assert setup.direction == SignalDirection.BUY
        assert setup.entry_zone == (101.0, 101.0)
        # SL anchors to the FIRST bar's low (99.8), not the range low (99.2).
        assert setup.stop_zone == (99.8, 99.8)
        # risk = 101.0 - 99.8 = 1.2; reward = 1.2 * 3.0 (default rr) = 3.6
        assert setup.target_zone == (104.6, 104.6)
        assert strategy._trade_taken_today is True
        assert setup.strategy_name == "OpeningRangeBreakoutStrategy"

    def test_bearish_breakout_with_volume_generates_setup_sl_at_first_bar(self) -> None:
        state = _new_state()
        strategy = OpeningRangeBreakoutStrategy(volume_lookback=5, session_timezone="UTC")
        _feed_opening_range(state, strategy)

        # close=99.0 < range_low(99.2); low=98.9 < range_low (attempted).
        breakout_bar = _bar(9, 35, 99.5, 99.6, 98.9, 99.0, volume=300.0)
        setup = _feed(state, strategy, breakout_bar)

        assert setup is not None
        assert setup.direction == SignalDirection.SELL
        assert setup.entry_zone == (99.0, 99.0)
        # SL anchors to the FIRST bar's high (100.2), not the range high (100.8).
        assert setup.stop_zone == (100.2, 100.2)
        # risk = 100.2 - 99.0 = 1.2; reward = 1.2 * 3.0 = 3.6
        assert setup.target_zone == (95.4, 95.4)

    def test_no_breakout_rejection_when_price_stays_inside_range(self) -> None:
        state = _new_state()
        strategy = OpeningRangeBreakoutStrategy(volume_lookback=5, session_timezone="UTC")
        _feed_opening_range(state, strategy)
        strategy.diagnostics.reset()  # bar 5 itself already recorded one NO_BREAKOUT

        inside_bar = _bar(9, 35, 100.0, 100.5, 99.8, 100.2, volume=300.0)
        result = _feed(state, strategy, inside_bar)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_BREAKOUT] == 1

    def test_wrong_close_side_rejection_when_wick_fails_to_confirm(self) -> None:
        state = _new_state()
        strategy = OpeningRangeBreakoutStrategy(volume_lookback=5, session_timezone="UTC")
        _feed_opening_range(state, strategy)

        # high=100.9 pokes above range_high(100.8) (attempted breakout), but
        # close=100.6 falls back inside the range -- not confirmed.
        wick_bar = _bar(9, 35, 100.5, 100.9, 100.3, 100.6, volume=300.0)
        result = _feed(state, strategy, wick_bar)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.WRONG_CLOSE_SIDE] == 1

    def test_no_volume_spike_rejection(self) -> None:
        state = _new_state()
        strategy = OpeningRangeBreakoutStrategy(volume_lookback=5, session_timezone="UTC")
        _feed_opening_range(state, strategy)

        # Same breakout shape as the successful test, but baseline volume.
        weak_volume_bar = _bar(9, 35, 100.5, 101.1, 100.4, 101.0, volume=100.0)
        result = _feed(state, strategy, weak_volume_bar)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_VOLUME_SPIKE] == 1


class TestOneTradePerDay:
    """Daily trade guard and next-day reset."""

    def test_trade_already_taken_today_blocks_further_signals(self) -> None:
        state = _new_state()
        strategy = OpeningRangeBreakoutStrategy(volume_lookback=5, session_timezone="UTC")
        _feed_opening_range(state, strategy)

        breakout_bar = _bar(9, 35, 100.5, 101.1, 100.4, 101.0, volume=300.0)
        setup = _feed(state, strategy, breakout_bar)
        assert setup is not None

        another_breakout = _bar(9, 36, 101.0, 101.5, 100.8, 101.4, volume=300.0)
        result = _feed(state, strategy, another_breakout)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.TRADE_ALREADY_TAKEN_TODAY] == 1

    def test_new_day_resets_state_and_allows_a_new_trade(self) -> None:
        state = _new_state()
        strategy = OpeningRangeBreakoutStrategy(volume_lookback=5, session_timezone="UTC")
        _feed_opening_range(state, strategy)

        breakout_bar = _bar(9, 35, 100.5, 101.1, 100.4, 101.0, volume=300.0)
        setup = _feed(state, strategy, breakout_bar)
        assert setup is not None
        assert strategy._trade_taken_today is True

        next_day_first_bar = _bar(9, 30, 100.0, 100.2, 99.8, 100.1, day=6)
        result = _feed(state, strategy, next_day_first_bar)

        assert result is None
        assert strategy._trade_taken_today is False
        assert strategy._range_ready is False
        assert strategy._range_count == 1
        assert strategy.diagnostics.rejections[RejectionReason.RANGE_NOT_READY] >= 1


class TestDiagnosticsAndConfig:
    """Diagnostics bookkeeping and config-overlay wiring."""

    def test_reset_clears_diagnostics_and_day_state(self) -> None:
        state = _new_state()
        strategy = OpeningRangeBreakoutStrategy(volume_lookback=5, session_timezone="UTC")
        _feed_opening_range(state, strategy)
        assert strategy._range_ready is True

        strategy.reset()
        assert strategy._range_ready is False
        assert strategy._range_count == 0
        assert strategy._trade_taken_today is False
        assert strategy.diagnostics.evaluations == 0
        assert strategy.diagnostics.rejections == {}

    def test_config_overlay_applies_all_fields(self) -> None:
        from datetime import time

        from strategy.opening_range_breakout import OpeningRangeBreakoutConfig

        config = OpeningRangeBreakoutConfig(
            volume_multiplier=2.0,
            volume_lookback=10,
            risk_reward=2.0,
            range_bars=3,
            session_start=time(8, 0),
            session_timezone="UTC",
        )
        strategy = OpeningRangeBreakoutStrategy(config=config)
        assert strategy.volume_multiplier == 2.0
        assert strategy.volume_lookback == 10
        assert strategy.risk_reward == 2.0
        assert strategy.range_bars == 3
        assert strategy.session_start == time(8, 0)
        assert strategy.session_timezone == "UTC"
