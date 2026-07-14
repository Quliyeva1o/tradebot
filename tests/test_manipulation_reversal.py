"""Unit tests for ManipulationReversalStrategy (Strategy #5, "9:30 NY Manipulation Reversal").

Uses a bare MarketState (append_bar/get_latest_bar only), same approach as
the other ported strategies' tests. session_timezone="UTC" throughout to
avoid DST complications in test bar construction.
"""

from datetime import UTC, datetime, time

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState
from strategy.diagnostics import RejectionReason
from strategy.manipulation_reversal import (
    ManipulationReversalConfig,
    ManipulationReversalStrategy,
)


def _bar(hour: int, minute: int, o: float, h: float, lo: float, c: float, day: int = 5) -> Bar:
    return Bar(
        timestamp=datetime(2026, 1, day, hour, minute, tzinfo=UTC),
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=100.0,
    )


def _new_state() -> MarketState:
    return MarketState(symbol="USTEC", timeframe=Timeframe.M1)


def _feed(state: MarketState, strategy: ManipulationReversalStrategy, bar: Bar):
    state.append_bar(bar)
    return strategy.evaluate(state)


def _new_strategy() -> ManipulationReversalStrategy:
    return ManipulationReversalStrategy(session_timezone="UTC")


REFERENCE_BAR = _bar(9, 30, 100.0, 100.5, 99.5, 100.1)


class TestReferenceCandle:
    """Reference (09:30) candle capture."""

    def test_reference_bar_freezes_high_low_and_rejects(self) -> None:
        state = _new_state()
        strategy = _new_strategy()
        result = _feed(state, strategy, REFERENCE_BAR)

        assert result is None
        assert strategy._reference_ready is True
        assert strategy._reference_high == 100.5
        assert strategy._reference_low == 99.5
        assert strategy.diagnostics.rejections[RejectionReason.REFERENCE_CANDLE_NOT_READY] == 1

    def test_bars_before_reference_are_rejected(self) -> None:
        state = _new_state()
        strategy = _new_strategy()
        early_bar = _bar(9, 15, 100.0, 100.2, 99.8, 100.1)
        result = _feed(state, strategy, early_bar)

        assert result is None
        assert strategy._reference_ready is False
        assert strategy.diagnostics.rejections[RejectionReason.REFERENCE_CANDLE_NOT_READY] == 1


class TestBullishFullCycle:
    """Full bullish scenario: breakout -> running max -> trap freeze -> reversal -> BUY."""

    def test_running_max_updates_while_still_green(self) -> None:
        state = _new_state()
        strategy = _new_strategy()
        _feed(state, strategy, REFERENCE_BAR)

        # Breakout: high(101.0) > ref_high(100.5). Green (close>open) -> still running.
        b1 = _bar(9, 31, 100.1, 101.0, 100.0, 100.8)
        r1 = _feed(state, strategy, b1)
        assert r1 is None
        assert strategy._scenario == SignalDirection.BUY
        assert strategy._running_extreme == 101.0
        assert strategy._trap_frozen is False
        assert strategy.diagnostics.rejections[RejectionReason.TRAP_NOT_FROZEN] == 1

        # New higher high, still green -> running max updates further.
        b2 = _bar(9, 32, 100.8, 101.5, 100.5, 101.3)
        r2 = _feed(state, strategy, b2)
        assert r2 is None
        assert strategy._running_extreme == 101.5
        assert strategy._trap_frozen is False

    def test_first_red_bar_freezes_trap_at_running_max(self) -> None:
        state = _new_state()
        strategy = _new_strategy()
        _feed(state, strategy, REFERENCE_BAR)
        _feed(state, strategy, _bar(9, 31, 100.1, 101.0, 100.0, 100.8))
        _feed(state, strategy, _bar(9, 32, 100.8, 101.5, 100.5, 101.3))

        # Red bar (close<open); high(101.4) < running_extreme(101.5) -> no update, freeze at 101.5.
        red_bar = _bar(9, 33, 101.3, 101.4, 100.9, 101.0)
        result = _feed(state, strategy, red_bar)

        assert result is None
        assert strategy._trap_frozen is True
        assert strategy._trap_point == 101.5
        assert strategy.diagnostics.rejections[RejectionReason.NO_REVERSAL_YET] == 1

    def test_manipulation_continues_until_first_green_triggers_buy_entry(self) -> None:
        state = _new_state()
        strategy = _new_strategy()
        _feed(state, strategy, REFERENCE_BAR)
        _feed(state, strategy, _bar(9, 31, 100.1, 101.0, 100.0, 100.8))
        _feed(state, strategy, _bar(9, 32, 100.8, 101.5, 100.5, 101.3))
        _feed(state, strategy, _bar(9, 33, 101.3, 101.4, 100.9, 101.0))  # freezes trap=101.5

        # Manipulation continues: another red bar, no reversal yet.
        still_red = _bar(9, 34, 101.0, 101.1, 100.5, 100.6)
        r = _feed(state, strategy, still_red)
        assert r is None
        assert strategy.diagnostics.rejections[RejectionReason.NO_REVERSAL_YET] == 2

        # First green bar since the freeze -> BUY entry at its close.
        reversal_bar = _bar(9, 35, 100.6, 100.9, 100.4, 100.8)
        setup = _feed(state, strategy, reversal_bar)

        assert setup is not None
        assert setup.direction == SignalDirection.BUY
        assert setup.entry_zone == (100.8, 100.8)
        # risk = trap(101.5) - entry(100.8) = 0.7 -> SL = 100.8 - 0.7 = 100.1
        assert setup.stop_zone == (100.1, 100.1)
        # TP1 = trap = 101.5 (exactly 1R)
        assert setup.target_zone == (101.5, 101.5)
        # TP2 = entry + 2*risk = 100.8 + 1.4 = 102.2
        assert setup.conditional_tp_extension_price == 102.2
        assert setup.conditional_tp_extension_bars == 3
        assert setup.strategy_name == "ManipulationReversalStrategy"
        assert strategy._trade_taken_today is True

    def test_breakout_bar_itself_being_opposite_color_freezes_trap_same_bar(self) -> None:
        state = _new_state()
        strategy = _new_strategy()
        _feed(state, strategy, REFERENCE_BAR)

        # Breakout AND red on the same bar: high(100.7) > ref_high(100.5), close<open.
        breakout_red_bar = _bar(9, 31, 100.6, 100.7, 100.0, 100.1)
        result = _feed(state, strategy, breakout_red_bar)

        assert result is None
        assert strategy._scenario == SignalDirection.BUY
        assert strategy._trap_frozen is True
        assert strategy._trap_point == 100.7
        assert strategy.diagnostics.rejections[RejectionReason.NO_REVERSAL_YET] == 1

        # Next bar green -> entry.
        reversal_bar = _bar(9, 32, 100.1, 100.4, 100.0, 100.3)
        setup = _feed(state, strategy, reversal_bar)
        assert setup is not None
        assert setup.direction == SignalDirection.BUY
        assert setup.entry_zone == (100.3, 100.3)


class TestBearishFullCycle:
    """Full bearish scenario: breakout down -> running min -> trap freeze -> reversal -> SELL."""

    def test_manipulation_continues_until_first_red_triggers_sell_entry(self) -> None:
        state = _new_state()
        strategy = _new_strategy()
        _feed(state, strategy, REFERENCE_BAR)

        # Breakout down: low(99.0) < ref_low(99.5). Red -> still running.
        _feed(state, strategy, _bar(9, 31, 99.9, 100.0, 99.0, 99.2))
        assert strategy._scenario == SignalDirection.SELL
        assert strategy._running_extreme == 99.0

        # Lower low, still red -> running min updates further.
        _feed(state, strategy, _bar(9, 32, 99.2, 99.3, 98.5, 98.8))
        assert strategy._running_extreme == 98.5
        assert strategy._trap_frozen is False

        # First green bar -> freezes trap at 98.5 (low 98.6 doesn't beat it).
        freeze_bar = _bar(9, 33, 98.8, 99.0, 98.6, 98.95)
        r = _feed(state, strategy, freeze_bar)
        assert r is None
        assert strategy._trap_frozen is True
        assert strategy._trap_point == 98.5

        # Manipulation continues up (still green) -> no reversal yet.
        still_green = _bar(9, 34, 98.95, 99.2, 98.9, 99.1)
        r2 = _feed(state, strategy, still_green)
        assert r2 is None

        # First red bar since the freeze -> SELL entry at its close.
        reversal_bar = _bar(9, 35, 99.1, 99.15, 98.7, 98.8)
        setup = _feed(state, strategy, reversal_bar)

        assert setup is not None
        assert setup.direction == SignalDirection.SELL
        assert setup.entry_zone == (98.8, 98.8)
        # risk = entry(98.8) - trap(98.5) = 0.3 -> SL = 98.8 + 0.3 = 99.1
        assert setup.stop_zone == (99.1, 99.1)
        assert setup.target_zone == (98.5, 98.5)
        # TP2 = entry - 2*risk = 98.8 - 0.6 = 98.2
        assert setup.conditional_tp_extension_price == 98.2


class TestNonPositiveRisk:
    """A degenerate reversal bar whose close crosses back past the trap is rejected."""

    def test_entry_beyond_trap_is_rejected(self) -> None:
        state = _new_state()
        strategy = _new_strategy()
        _feed(state, strategy, REFERENCE_BAR)
        _feed(state, strategy, _bar(9, 31, 100.1, 100.6, 100.0, 100.55))  # breakout, green
        _feed(state, strategy, _bar(9, 32, 100.55, 100.58, 100.3, 100.35))  # red, freezes trap=100.6

        # Green reversal bar whose CLOSE is already above the trap (100.6).
        bad_reversal = _bar(9, 33, 100.35, 100.7, 100.3, 100.65)
        result = _feed(state, strategy, bad_reversal)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.NON_POSITIVE_RISK] == 1


class TestOneTradePerDay:
    """Daily trade guard and next-day reset."""

    def _run_full_bullish_cycle(self, state: MarketState, strategy: ManipulationReversalStrategy):
        _feed(state, strategy, REFERENCE_BAR)
        _feed(state, strategy, _bar(9, 31, 100.1, 101.0, 100.0, 100.8))
        _feed(state, strategy, _bar(9, 32, 100.8, 101.5, 100.5, 101.3))
        _feed(state, strategy, _bar(9, 33, 101.3, 101.4, 100.9, 101.0))
        return _feed(state, strategy, _bar(9, 35, 100.6, 100.9, 100.4, 100.8))

    def test_trade_already_taken_blocks_further_signals_same_day(self) -> None:
        state = _new_state()
        strategy = _new_strategy()
        setup = self._run_full_bullish_cycle(state, strategy)
        assert setup is not None

        another_bar = _bar(9, 36, 100.8, 102.0, 100.7, 101.9)
        result = _feed(state, strategy, another_bar)

        assert result is None
        assert strategy.diagnostics.rejections[RejectionReason.TRADE_ALREADY_TAKEN] == 1

    def test_new_day_reference_bar_resets_state_and_allows_a_new_trade(self) -> None:
        state = _new_state()
        strategy = _new_strategy()
        setup = self._run_full_bullish_cycle(state, strategy)
        assert setup is not None
        assert strategy._trade_taken_today is True

        next_day_reference = _bar(9, 30, 100.0, 100.3, 99.7, 100.1, day=6)
        result = _feed(state, strategy, next_day_reference)

        assert result is None
        assert strategy._trade_taken_today is False
        assert strategy._scenario is None
        assert strategy._trap_frozen is False
        assert strategy._reference_high == 100.3
        assert strategy._reference_low == 99.7


class TestDiagnosticsAndConfig:
    """Diagnostics bookkeeping and config-overlay wiring."""

    def test_reset_clears_diagnostics_and_day_state(self) -> None:
        state = _new_state()
        strategy = _new_strategy()
        _feed(state, strategy, REFERENCE_BAR)
        _feed(state, strategy, _bar(9, 31, 100.1, 101.0, 100.0, 100.8))
        assert strategy._scenario == SignalDirection.BUY

        strategy.reset()
        assert strategy._reference_ready is False
        assert strategy._scenario is None
        assert strategy._trap_frozen is False
        assert strategy._trade_taken_today is False
        assert strategy.diagnostics.evaluations == 0
        assert strategy.diagnostics.rejections == {}

    def test_config_overlay_applies_all_fields(self) -> None:
        config = ManipulationReversalConfig(
            reference_time=time(8, 0),
            session_timezone="UTC",
            tp_extension_bars=5,
            tp_extension_multiple=3.0,
        )
        strategy = ManipulationReversalStrategy(config=config)
        assert strategy.reference_time == time(8, 0)
        assert strategy.session_timezone == "UTC"
        assert strategy.tp_extension_bars == 5
        assert strategy.tp_extension_multiple == 3.0


class TestRecommendedMaxHoldingBars:
    """No session-length concept for this strategy -- always None."""

    def test_default_is_none(self) -> None:
        strategy = _new_strategy()
        assert strategy.recommended_max_holding_bars(Timeframe.M1) is None
        assert strategy.recommended_max_holding_bars(Timeframe.M5) is None
