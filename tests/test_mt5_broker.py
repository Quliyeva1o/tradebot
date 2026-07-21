"""Unit tests for MT5Broker (execution/mt5_broker.py).

MT5 API calls are mocked throughout; no real terminal connection is made.
Mocking follows the same pattern as tests/test_mt5_connector.py: patch the
`mt5` name inside the module under test, using SimpleNamespace stand-ins for
MT5's return objects (only the attributes actually read are set).
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.models import AccountInfo, OrderType
from execution.models import OrderRequest, OrderResult, Position
from execution.mt5_broker import MT5Broker
from mt5.connector import MT5Connector


def _order_send_result(
    retcode: int = 10009,  # TRADE_RETCODE_DONE
    order: int = 555,
    price: float = 29200.0,
    volume: float = 0.1,
    comment: str = "",
) -> SimpleNamespace:
    """Minimal stand-in for MT5's OrderSendResult, exposing only what we read."""
    return SimpleNamespace(retcode=retcode, order=order, price=price, volume=volume, comment=comment)


def _tick(bid: float = 29199.0, ask: float = 29201.0) -> SimpleNamespace:
    """Minimal stand-in for MT5's Tick info, exposing only what we read (.bid/.ask)."""
    return SimpleNamespace(bid=bid, ask=ask)


def _mt5_position(
    ticket: int = 777,
    symbol: str = "USTEC",
    type_: int = 0,  # POSITION_TYPE_BUY
    volume: float = 0.5,
    price_open: float = 29000.0,
    price_current: float = 29050.0,
    sl: float = 0.0,
    tp: float = 0.0,
    profit: float = 25.0,
    time: int = 1735689600,
) -> SimpleNamespace:
    """Minimal stand-in for MT5's TradePosition, exposing only what we read."""
    return SimpleNamespace(
        ticket=ticket,
        symbol=symbol,
        type=type_,
        volume=volume,
        price_open=price_open,
        price_current=price_current,
        sl=sl,
        tp=tp,
        profit=profit,
        time=time,
    )


class TestConnect:
    """Tests for MT5Broker.connect()."""

    def test_delegates_to_connector_connect(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.connect.return_value = True
        broker = MT5Broker(connector)

        assert broker.connect() is True
        connector.connect.assert_called_once_with()

    def test_returns_false_when_connector_fails(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.connect.return_value = False
        broker = MT5Broker(connector)

        assert broker.connect() is False


class TestGetAccountInfo:
    """Tests for MT5Broker.get_account_info()."""

    def test_delegates_to_connector_fetch_account_info(self) -> None:
        connector = Mock(spec=MT5Connector)
        expected = AccountInfo(balance=10_000.0, equity=10_000.0, margin=0.0, free_margin=10_000.0)
        connector.fetch_account_info.return_value = expected
        broker = MT5Broker(connector)

        assert broker.get_account_info() is expected
        connector.fetch_account_info.assert_called_once_with()

    def test_propagates_connector_failure(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.fetch_account_info.side_effect = RuntimeError("account_info returned None")
        broker = MT5Broker(connector)

        with pytest.raises(RuntimeError, match="account_info returned None"):
            broker.get_account_info()


class TestPlaceOrder:
    """Tests for MT5Broker.place_order()."""

    def test_market_buy_uses_ask_price_when_price_not_given(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1)

        with (
            patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
            patch("execution.mt5_broker.mt5.symbol_info_tick", return_value=_tick(bid=100.0, ask=101.0)),
            patch(
                "execution.mt5_broker.mt5.order_send", return_value=_order_send_result()
            ) as mock_send,
        ):
            result = broker.place_order(order)

        assert isinstance(result, OrderResult)
        assert result.success is True
        sent_request = mock_send.call_args[0][0]
        assert sent_request["price"] == 101.0
        assert sent_request["action"] == 1  # TRADE_ACTION_DEAL

    def test_market_sell_uses_bid_price_when_price_not_given(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(symbol="USTEC", order_type=OrderType.SELL_MARKET, volume=0.1)

        with (
            patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
            patch("execution.mt5_broker.mt5.symbol_info_tick", return_value=_tick(bid=100.0, ask=101.0)),
            patch("execution.mt5_broker.mt5.order_send", return_value=_order_send_result()) as mock_send,
        ):
            broker.place_order(order)

        sent_request = mock_send.call_args[0][0]
        assert sent_request["price"] == 100.0

    def test_explicit_price_skips_tick_lookup(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1, price=29500.0)

        with (
            patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
            patch("execution.mt5_broker.mt5.symbol_info_tick") as mock_tick,
            patch("execution.mt5_broker.mt5.order_send", return_value=_order_send_result()) as mock_send,
        ):
            broker.place_order(order)

        mock_tick.assert_not_called()
        assert mock_send.call_args[0][0]["price"] == 29500.0

    def test_pending_order_uses_pending_action_and_given_price(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(
            symbol="USTEC", order_type=OrderType.BUY_LIMIT, volume=0.2, price=29000.0
        )

        with (
            patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
            patch("execution.mt5_broker.mt5.order_send", return_value=_order_send_result()) as mock_send,
        ):
            broker.place_order(order)

        sent_request = mock_send.call_args[0][0]
        assert sent_request["action"] == 5  # TRADE_ACTION_PENDING
        assert sent_request["type"] == 2  # ORDER_TYPE_BUY_LIMIT
        assert sent_request["price"] == 29000.0

    def test_pending_order_without_price_raises_value_error(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(symbol="USTEC", order_type=OrderType.SELL_STOP, volume=0.2)

        with patch("execution.mt5_broker.mt5.symbol_select", return_value=True):
            with pytest.raises(ValueError, match="price is required"):
                broker.place_order(order)

    def test_stop_loss_and_take_profit_included_when_provided(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(
            symbol="USTEC",
            order_type=OrderType.BUY_MARKET,
            volume=0.1,
            price=29000.0,
            stop_loss=28900.0,
            take_profit=29200.0,
            comment="test-signal",
        )

        with (
            patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
            patch("execution.mt5_broker.mt5.order_send", return_value=_order_send_result()) as mock_send,
        ):
            broker.place_order(order)

        sent_request = mock_send.call_args[0][0]
        assert sent_request["sl"] == 28900.0
        assert sent_request["tp"] == 29200.0
        assert sent_request["comment"] == "test-signal"

    def test_raises_when_symbol_unavailable(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(symbol="UNKNOWN", order_type=OrderType.BUY_MARKET, volume=0.1)

        with patch("execution.mt5_broker.mt5.symbol_select", return_value=False):
            with pytest.raises(RuntimeError, match="not available"):
                broker.place_order(order)

    def test_raises_when_tick_unavailable(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1)

        with (
            patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
            patch("execution.mt5_broker.mt5.symbol_info_tick", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="symbol_info_tick"):
                broker.place_order(order)

    def test_raises_when_order_send_returns_none(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1, price=100.0)

        with (
            patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
            patch("execution.mt5_broker.mt5.order_send", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="order_send"):
                broker.place_order(order)

    def test_broker_rejection_returns_failed_order_result_without_raising(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1, price=100.0)
        rejected = _order_send_result(retcode=10019, comment="No money")  # TRADE_RETCODE_NO_MONEY

        with (
            patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
            patch("execution.mt5_broker.mt5.order_send", return_value=rejected),
        ):
            result = broker.place_order(order)

        assert result.success is False
        assert result.retcode == 10019
        assert result.comment == "No money"


class TestCancelOrder:
    """Tests for MT5Broker.cancel_order()."""

    def test_success_returns_true(self) -> None:
        broker = MT5Broker()
        with patch(
            "execution.mt5_broker.mt5.order_send", return_value=_order_send_result(retcode=10009)
        ) as mock_send:
            assert broker.cancel_order("555") is True

        sent_request = mock_send.call_args[0][0]
        assert sent_request == {"action": 8, "order": 555}  # TRADE_ACTION_REMOVE

    def test_rejection_returns_false(self) -> None:
        broker = MT5Broker()
        with patch("execution.mt5_broker.mt5.order_send", return_value=_order_send_result(retcode=10025)):
            assert broker.cancel_order("555") is False

    def test_raises_when_order_send_returns_none(self) -> None:
        broker = MT5Broker()
        with patch("execution.mt5_broker.mt5.order_send", return_value=None):
            with pytest.raises(RuntimeError, match="order_send"):
                broker.cancel_order("555")

    def test_raises_value_error_for_non_integer_order_id(self) -> None:
        broker = MT5Broker()
        with pytest.raises(ValueError, match="valid integer ticket"):
            broker.cancel_order("not-a-ticket")


class TestClosePosition:
    """Tests for MT5Broker.close_position()."""

    def test_closes_buy_position_at_bid_with_opposite_sell(self) -> None:
        broker = MT5Broker()
        with (
            patch(
                "execution.mt5_broker.mt5.positions_get",
                return_value=[_mt5_position(ticket=777, type_=0, volume=0.3)],
            ),
            patch("execution.mt5_broker.mt5.symbol_info_tick", return_value=_tick(bid=100.0, ask=101.0)),
            patch(
                "execution.mt5_broker.mt5.order_send", return_value=_order_send_result(order=999)
            ) as mock_send,
        ):
            result = broker.close_position("777")

        assert result.success is True
        assert result.position_id == "777"
        sent_request = mock_send.call_args[0][0]
        assert sent_request["type"] == 1  # ORDER_TYPE_SELL
        assert sent_request["price"] == 100.0
        assert sent_request["position"] == 777
        assert sent_request["volume"] == 0.3

    def test_closes_sell_position_at_ask_with_opposite_buy(self) -> None:
        broker = MT5Broker()
        with (
            patch(
                "execution.mt5_broker.mt5.positions_get",
                return_value=[_mt5_position(ticket=777, type_=1)],
            ),
            patch("execution.mt5_broker.mt5.symbol_info_tick", return_value=_tick(bid=100.0, ask=101.0)),
            patch("execution.mt5_broker.mt5.order_send", return_value=_order_send_result()) as mock_send,
        ):
            broker.close_position("777")

        sent_request = mock_send.call_args[0][0]
        assert sent_request["type"] == 0  # ORDER_TYPE_BUY
        assert sent_request["price"] == 101.0

    def test_raises_value_error_for_non_integer_position_id(self) -> None:
        broker = MT5Broker()
        with pytest.raises(ValueError, match="valid integer ticket"):
            broker.close_position("not-a-ticket")

    def test_raises_when_positions_get_returns_none(self) -> None:
        broker = MT5Broker()
        with patch("execution.mt5_broker.mt5.positions_get", return_value=None):
            with pytest.raises(RuntimeError, match="positions_get"):
                broker.close_position("777")

    def test_raises_when_no_position_found_for_ticket(self) -> None:
        broker = MT5Broker()
        with patch("execution.mt5_broker.mt5.positions_get", return_value=[]):
            with pytest.raises(RuntimeError, match="No open position"):
                broker.close_position("777")

    def test_raises_when_tick_unavailable(self) -> None:
        broker = MT5Broker()
        with (
            patch("execution.mt5_broker.mt5.positions_get", return_value=[_mt5_position()]),
            patch("execution.mt5_broker.mt5.symbol_info_tick", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="symbol_info_tick"):
                broker.close_position("777")

    def test_raises_when_order_send_returns_none(self) -> None:
        broker = MT5Broker()
        with (
            patch("execution.mt5_broker.mt5.positions_get", return_value=[_mt5_position()]),
            patch("execution.mt5_broker.mt5.symbol_info_tick", return_value=_tick()),
            patch("execution.mt5_broker.mt5.order_send", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="order_send"):
                broker.close_position("777")

    def test_rejection_returns_failed_order_result(self) -> None:
        broker = MT5Broker()
        rejected = _order_send_result(retcode=10025, comment="No changes")
        with (
            patch("execution.mt5_broker.mt5.positions_get", return_value=[_mt5_position()]),
            patch("execution.mt5_broker.mt5.symbol_info_tick", return_value=_tick()),
            patch("execution.mt5_broker.mt5.order_send", return_value=rejected),
        ):
            result = broker.close_position("777")

        assert result.success is False
        assert result.comment == "No changes"


class TestGetOpenPositions:
    """Tests for MT5Broker.get_open_positions()."""

    def test_maps_mt5_positions_to_position_models(self) -> None:
        broker = MT5Broker()
        with patch("execution.mt5_broker.mt5.positions_get", return_value=[_mt5_position()]):
            positions = broker.get_open_positions()

        assert len(positions) == 1
        pos = positions[0]
        assert isinstance(pos, Position)
        assert pos.id == "777"
        assert pos.symbol == "USTEC"
        assert pos.order_type == OrderType.BUY_MARKET
        assert pos.volume == 0.5
        assert pos.open_price == 29000.0
        assert pos.current_price == 29050.0
        assert pos.profit == 25.0

    def test_zero_sl_tp_mapped_to_none(self) -> None:
        broker = MT5Broker()
        with patch(
            "execution.mt5_broker.mt5.positions_get", return_value=[_mt5_position(sl=0.0, tp=0.0)]
        ):
            positions = broker.get_open_positions()

        assert positions[0].stop_loss is None
        assert positions[0].take_profit is None

    def test_nonzero_sl_tp_preserved(self) -> None:
        broker = MT5Broker()
        with patch(
            "execution.mt5_broker.mt5.positions_get",
            return_value=[_mt5_position(sl=28900.0, tp=29200.0)],
        ):
            positions = broker.get_open_positions()

        assert positions[0].stop_loss == 28900.0
        assert positions[0].take_profit == 29200.0

    def test_sell_position_maps_to_sell_market(self) -> None:
        broker = MT5Broker()
        with patch(
            "execution.mt5_broker.mt5.positions_get", return_value=[_mt5_position(type_=1)]
        ):
            positions = broker.get_open_positions()

        assert positions[0].order_type == OrderType.SELL_MARKET

    def test_empty_positions_returns_empty_list(self) -> None:
        broker = MT5Broker()
        with patch("execution.mt5_broker.mt5.positions_get", return_value=[]):
            assert broker.get_open_positions() == []

    def test_raises_when_positions_get_returns_none(self) -> None:
        broker = MT5Broker()
        with patch("execution.mt5_broker.mt5.positions_get", return_value=None):
            with pytest.raises(RuntimeError, match="positions_get"):
                broker.get_open_positions()
