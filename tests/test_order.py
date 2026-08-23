"""Unit tests for execution/order.py (Order status-transition lifecycle)."""

from datetime import UTC, datetime

import pytest

from core.models import OrderType
from execution.models import OrderRequest
from execution.order import Order, OrderStatus


def _order(order_id: str = "1") -> Order:
    request = OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1)
    return Order(order_id=order_id, request=request)


class TestConstruction:
    """Tests for Order's default construction state."""

    def test_defaults_to_pending_with_no_fill_data(self) -> None:
        order = _order()
        assert order.status is OrderStatus.PENDING
        assert order.filled_at is None
        assert order.fill_price is None

    def test_created_at_defaults_to_now(self) -> None:
        before = datetime.now(UTC)
        order = _order()
        after = datetime.now(UTC)
        assert before <= order.created_at <= after


class TestFill:
    """Tests for Order.fill()."""

    def test_pending_to_filled_sets_price_and_timestamp(self) -> None:
        order = _order()
        order.fill(29_000.0)
        assert order.status is OrderStatus.FILLED
        assert order.fill_price == 29_000.0
        assert order.filled_at is not None

    def test_explicit_filled_at_is_preserved(self) -> None:
        order = _order()
        ts = datetime(2026, 1, 1, tzinfo=UTC)
        order.fill(29_000.0, filled_at=ts)
        assert order.filled_at == ts

    def test_non_positive_fill_price_raises(self) -> None:
        order = _order()
        with pytest.raises(ValueError, match="fill_price"):
            order.fill(0.0)
        assert order.status is OrderStatus.PENDING

    def test_filling_an_already_filled_order_raises(self) -> None:
        order = _order()
        order.fill(29_000.0)
        with pytest.raises(ValueError, match="Invalid order status transition"):
            order.fill(29_100.0)

    def test_filling_a_rejected_order_raises(self) -> None:
        order = _order()
        order.reject()
        with pytest.raises(ValueError, match="Invalid order status transition"):
            order.fill(29_000.0)

    def test_filling_a_cancelled_order_raises(self) -> None:
        order = _order()
        order.cancel()
        with pytest.raises(ValueError, match="Invalid order status transition"):
            order.fill(29_000.0)


class TestReject:
    """Tests for Order.reject()."""

    def test_pending_to_rejected(self) -> None:
        order = _order()
        order.reject()
        assert order.status is OrderStatus.REJECTED

    def test_rejecting_a_filled_order_raises(self) -> None:
        order = _order()
        order.fill(29_000.0)
        with pytest.raises(ValueError, match="Invalid order status transition"):
            order.reject()

    def test_rejecting_twice_raises(self) -> None:
        order = _order()
        order.reject()
        with pytest.raises(ValueError, match="Invalid order status transition"):
            order.reject()


class TestCancel:
    """Tests for Order.cancel()."""

    def test_pending_to_cancelled(self) -> None:
        order = _order()
        order.cancel()
        assert order.status is OrderStatus.CANCELLED

    def test_cancelling_a_filled_order_raises(self) -> None:
        order = _order()
        order.fill(29_000.0)
        with pytest.raises(ValueError, match="Invalid order status transition"):
            order.cancel()

    def test_cancelling_twice_raises(self) -> None:
        order = _order()
        order.cancel()
        with pytest.raises(ValueError, match="Invalid order status transition"):
            order.cancel()

    def test_error_message_includes_order_id(self) -> None:
        order = _order(order_id="abc-123")
        order.cancel()
        with pytest.raises(ValueError, match="abc-123"):
            order.cancel()
