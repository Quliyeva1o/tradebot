"""Regression tests for execution/models.py __post_init__ validation.

Mirrors the strategy config validation convention (core/validation.py:
require_positive/require_non_negative) so a malformed OrderRequest/
OrderResult/Position fails fast at construction instead of being sent to
a broker or silently accepted from one.
"""

import pytest

from core.models import OrderType
from execution.models import OrderRequest, OrderResult, Position


class TestOrderRequest:
    """Tests for OrderRequest.__post_init__ validation."""

    def test_typical_values_construct_without_error(self) -> None:
        order = OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1)
        assert order.volume == 0.1
        assert order.price is None

    def test_non_positive_volume_raises(self) -> None:
        with pytest.raises(ValueError, match="volume"):
            OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.0)

    def test_negative_deviation_raises(self) -> None:
        with pytest.raises(ValueError, match="deviation"):
            OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1, deviation=-1)

    def test_zero_deviation_is_allowed(self) -> None:
        order = OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1, deviation=0)
        assert order.deviation == 0

    def test_non_positive_price_raises(self) -> None:
        with pytest.raises(ValueError, match="price"):
            OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1, price=0.0)

    def test_non_positive_stop_loss_raises(self) -> None:
        with pytest.raises(ValueError, match="stop_loss"):
            OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1, stop_loss=0.0)

    def test_non_positive_take_profit_raises(self) -> None:
        with pytest.raises(ValueError, match="take_profit"):
            OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1, take_profit=0.0)

    def test_optional_price_fields_default_to_none(self) -> None:
        order = OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1)
        assert order.stop_loss is None
        assert order.take_profit is None


class TestOrderResult:
    """Tests for OrderResult.__post_init__ validation."""

    def test_typical_values_construct_without_error(self) -> None:
        result = OrderResult(success=True, order_id="123", price=100.0, volume=0.1)
        assert result.success is True

    def test_failed_result_with_zero_defaults_is_allowed(self) -> None:
        result = OrderResult(success=False, comment="rejected")
        assert result.price == 0.0
        assert result.volume == 0.0

    def test_negative_price_raises(self) -> None:
        with pytest.raises(ValueError, match="price"):
            OrderResult(success=True, price=-1.0)

    def test_negative_volume_raises(self) -> None:
        with pytest.raises(ValueError, match="volume"):
            OrderResult(success=True, volume=-1.0)


class TestPosition:
    """Tests for Position.__post_init__ validation."""

    def test_typical_values_construct_without_error(self) -> None:
        position = Position(
            id="1",
            symbol="USTEC",
            order_type=OrderType.BUY_MARKET,
            volume=0.5,
            open_price=29000.0,
            current_price=29050.0,
        )
        assert position.stop_loss is None

    def test_non_positive_volume_raises(self) -> None:
        with pytest.raises(ValueError, match="volume"):
            Position(
                id="1",
                symbol="USTEC",
                order_type=OrderType.BUY_MARKET,
                volume=0.0,
                open_price=29000.0,
                current_price=29050.0,
            )

    def test_non_positive_open_price_raises(self) -> None:
        with pytest.raises(ValueError, match="open_price"):
            Position(
                id="1",
                symbol="USTEC",
                order_type=OrderType.BUY_MARKET,
                volume=0.5,
                open_price=0.0,
                current_price=29050.0,
            )

    def test_negative_current_price_raises(self) -> None:
        with pytest.raises(ValueError, match="current_price"):
            Position(
                id="1",
                symbol="USTEC",
                order_type=OrderType.BUY_MARKET,
                volume=0.5,
                open_price=29000.0,
                current_price=-1.0,
            )

    def test_non_positive_stop_loss_raises(self) -> None:
        with pytest.raises(ValueError, match="stop_loss"):
            Position(
                id="1",
                symbol="USTEC",
                order_type=OrderType.BUY_MARKET,
                volume=0.5,
                open_price=29000.0,
                current_price=29050.0,
                stop_loss=0.0,
            )

    def test_non_positive_take_profit_raises(self) -> None:
        with pytest.raises(ValueError, match="take_profit"):
            Position(
                id="1",
                symbol="USTEC",
                order_type=OrderType.BUY_MARKET,
                volume=0.5,
                open_price=29000.0,
                current_price=29050.0,
                take_profit=0.0,
            )
