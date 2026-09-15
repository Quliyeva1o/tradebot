"""Tests for execution/level_fill.py -- how a resting order or a stop/target fills on one bar.

Prices are hand-picked so every expected fill can be read straight off the bar.
"""

from datetime import UTC, datetime

from core.models import Bar, OrderType, SignalDirection
from execution.level_fill import exit_fill, limit_fill


def _bar(o: float, h: float, l: float, c: float) -> Bar:
    return Bar(timestamp=datetime(2026, 9, 14, 14, 31, tzinfo=UTC), open=o, high=h, low=l, close=c, volume=1.0)


class TestLimitFill:
    def test_buy_limit_fills_at_its_price_when_the_bar_trades_down_to_it(self) -> None:
        assert limit_fill(OrderType.BUY_LIMIT, 100.0, _bar(103, 104, 99.5, 101)) == 100.0

    def test_buy_limit_fills_at_the_open_when_the_bar_opens_below_it(self) -> None:
        assert limit_fill(OrderType.BUY_LIMIT, 100.0, _bar(98, 101, 97, 99)) == 98.0

    def test_buy_limit_does_not_fill_when_the_low_stays_above_it(self) -> None:
        assert limit_fill(OrderType.BUY_LIMIT, 100.0, _bar(103, 104, 100.5, 101)) is None

    def test_sell_limit_fills_at_its_price_when_the_bar_trades_up_to_it(self) -> None:
        assert limit_fill(OrderType.SELL_LIMIT, 200.0, _bar(197, 201, 196, 199)) == 200.0

    def test_sell_limit_fills_at_the_open_when_the_bar_opens_above_it(self) -> None:
        assert limit_fill(OrderType.SELL_LIMIT, 200.0, _bar(203, 204, 199, 202)) == 203.0


class TestExitFillLong:
    """Long: stop 90, target 150."""

    def test_stop_fills_at_the_stop(self) -> None:
        assert exit_fill(SignalDirection.BUY, 90.0, 150.0, _bar(95, 96, 89, 91),
                         entry_bar=False, after_break=False) == (90.0, "SL")

    def test_gap_below_the_stop_fills_at_the_open(self) -> None:
        assert exit_fill(SignalDirection.BUY, 90.0, 150.0, _bar(85, 88, 84, 87),
                         entry_bar=False, after_break=False) == (85.0, "SL")

    def test_on_the_entry_bar_the_open_came_before_the_fill_so_the_stop_price_holds(self) -> None:
        assert exit_fill(SignalDirection.BUY, 90.0, 150.0, _bar(85, 101, 84, 87),
                         entry_bar=True, after_break=False) == (90.0, "SL")

    def test_stop_wins_when_one_bar_touches_both(self) -> None:
        assert exit_fill(SignalDirection.BUY, 90.0, 150.0, _bar(120, 151, 89, 130),
                         entry_bar=False, after_break=False) == (90.0, "SL")

    def test_target_fills_at_the_target(self) -> None:
        assert exit_fill(SignalDirection.BUY, 90.0, 150.0, _bar(140, 150, 139, 149),
                         entry_bar=False, after_break=False) == (150.0, "TP")

    def test_gap_above_the_target_fills_at_the_open(self) -> None:
        assert exit_fill(SignalDirection.BUY, 90.0, 150.0, _bar(155, 156, 152, 153),
                         entry_bar=False, after_break=False) == (155.0, "TP")

    def test_first_bar_after_a_break_stopped_out_fills_at_its_close_when_that_is_worse(self) -> None:
        # The open is a stale re-quote of the pre-break close; the real price is nearer the close.
        assert exit_fill(SignalDirection.BUY, 90.0, 150.0, _bar(95, 96, 80, 84),
                         entry_bar=False, after_break=True) == (84.0, "SL")

    def test_first_bar_after_a_break_closing_back_above_the_stop_fills_at_the_stop(self) -> None:
        assert exit_fill(SignalDirection.BUY, 90.0, 150.0, _bar(95, 96, 80, 92),
                         entry_bar=False, after_break=True) == (90.0, "SL")

    def test_bar_inside_both_levels_does_not_exit(self) -> None:
        assert exit_fill(SignalDirection.BUY, 90.0, 150.0, _bar(100, 140, 91, 120),
                         entry_bar=False, after_break=False) is None


class TestExitFillShort:
    """Short: stop 210, target 165."""

    def test_stop_fills_at_the_stop(self) -> None:
        assert exit_fill(SignalDirection.SELL, 210.0, 165.0, _bar(200, 211, 199, 205),
                         entry_bar=False, after_break=False) == (210.0, "SL")

    def test_gap_above_the_stop_fills_at_the_open(self) -> None:
        assert exit_fill(SignalDirection.SELL, 210.0, 165.0, _bar(215, 216, 212, 214),
                         entry_bar=False, after_break=False) == (215.0, "SL")

    def test_target_fills_at_the_target(self) -> None:
        assert exit_fill(SignalDirection.SELL, 210.0, 165.0, _bar(170, 171, 165, 168),
                         entry_bar=False, after_break=False) == (165.0, "TP")
