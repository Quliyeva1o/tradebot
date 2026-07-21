"""Unit tests for execution/fill_simulator.py.

Includes a direct cross-check against backtest/engine.py's own entry-price
arithmetic (BacktestEngine.run(), pending-setup fill block, BUY:
`entry_price = limit_price + spread / 2 + slippage`; SELL:
`entry_price = limit_price - spread / 2 - slippage`) to confirm
simulate_market_fill() actually reproduces the same half-spread +
full-slippage, worse-price-for-the-trader convention BacktestConfig.spread's
docstring describes, rather than merely claiming to in its own docstring.
"""

import pytest

from core.models import OrderType
from execution.fill_simulator import simulate_market_fill


def _backtest_engine_entry_price(direction_is_buy: bool, limit_price: float, spread: float, slippage: float) -> float:
    """Reproduces BacktestEngine.run()'s pending-setup entry-price formula verbatim.

    See backtest/engine.py, the pending-setup-execution block (~lines
    352-392): entry_price = limit_price +/- spread / 2 +/- slippage,
    BUY paying more, SELL receiving less.
    """
    if direction_is_buy:
        return limit_price + spread / 2 + slippage
    return limit_price - spread / 2 - slippage


class TestCrossCheckAgainstBacktestEngine:
    """Confirms simulate_market_fill() matches BacktestEngine.run()'s formula exactly."""

    @pytest.mark.parametrize(
        ("price", "spread", "slippage"),
        [
            (29_000.0, 2.0, 0.5),
            (1.10500, 0.00020, 0.00005),
            (100.0, 0.0, 0.0),
        ],
    )
    def test_buy_market_matches_backtest_buy_entry_formula(
        self, price: float, spread: float, slippage: float
    ) -> None:
        expected = _backtest_engine_entry_price(True, price, spread, slippage)
        actual = simulate_market_fill(OrderType.BUY_MARKET, price, spread, slippage)
        assert actual == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("price", "spread", "slippage"),
        [
            (29_000.0, 2.0, 0.5),
            (1.10500, 0.00020, 0.00005),
            (100.0, 0.0, 0.0),
        ],
    )
    def test_sell_market_matches_backtest_sell_entry_formula(
        self, price: float, spread: float, slippage: float
    ) -> None:
        expected = _backtest_engine_entry_price(False, price, spread, slippage)
        actual = simulate_market_fill(OrderType.SELL_MARKET, price, spread, slippage)
        assert actual == pytest.approx(expected)

    def test_shared_worked_example(self) -> None:
        """USTEC-scale worked example: bar open 29200.0, spread 2.0, slippage 0.5.

        BUY: 29200.0 + 1.0 (half spread) + 0.5 (slippage) = 29201.5
        SELL: 29200.0 - 1.0 (half spread) - 0.5 (slippage) = 29198.5
        """
        buy_fill = simulate_market_fill(OrderType.BUY_MARKET, 29_200.0, 2.0, 0.5)
        sell_fill = simulate_market_fill(OrderType.SELL_MARKET, 29_200.0, 2.0, 0.5)

        assert buy_fill == pytest.approx(29_201.5)
        assert sell_fill == pytest.approx(29_198.5)
        assert buy_fill == pytest.approx(_backtest_engine_entry_price(True, 29_200.0, 2.0, 0.5))
        assert sell_fill == pytest.approx(_backtest_engine_entry_price(False, 29_200.0, 2.0, 0.5))


class TestSpreadApplication:
    """Tests that only half the spread is applied (BacktestConfig.spread convention)."""

    def test_buy_applies_half_spread(self) -> None:
        fill = simulate_market_fill(OrderType.BUY_MARKET, 100.0, spread=4.0, slippage=0.0)
        assert fill == pytest.approx(102.0)

    def test_sell_applies_half_spread(self) -> None:
        fill = simulate_market_fill(OrderType.SELL_MARKET, 100.0, spread=4.0, slippage=0.0)
        assert fill == pytest.approx(98.0)

    def test_zero_spread_is_a_noop_on_spread(self) -> None:
        assert simulate_market_fill(OrderType.BUY_MARKET, 100.0, spread=0.0, slippage=0.0) == 100.0


class TestSlippageApplication:
    """Tests that slippage is applied in full, in the adverse direction."""

    def test_buy_slippage_increases_fill_price(self) -> None:
        fill = simulate_market_fill(OrderType.BUY_MARKET, 100.0, spread=0.0, slippage=0.3)
        assert fill == pytest.approx(100.3)

    def test_sell_slippage_decreases_fill_price(self) -> None:
        fill = simulate_market_fill(OrderType.SELL_MARKET, 100.0, spread=0.0, slippage=0.3)
        assert fill == pytest.approx(99.7)

    def test_spread_and_slippage_compound(self) -> None:
        fill = simulate_market_fill(OrderType.BUY_MARKET, 100.0, spread=2.0, slippage=0.3)
        assert fill == pytest.approx(101.3)


class TestUnsupportedOrderTypes:
    """Tests that pending order types are rejected (no fill model for them here)."""

    @pytest.mark.parametrize(
        "order_type",
        [OrderType.BUY_LIMIT, OrderType.SELL_LIMIT, OrderType.BUY_STOP, OrderType.SELL_STOP],
    )
    def test_pending_order_types_raise(self, order_type: OrderType) -> None:
        with pytest.raises(ValueError, match="market order types"):
            simulate_market_fill(order_type, 100.0, spread=1.0, slippage=0.0)
