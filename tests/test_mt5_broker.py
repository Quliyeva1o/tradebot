"""Unit tests for MT5Broker (execution/mt5_broker.py).

MT5 API calls are mocked throughout; no real terminal connection is made.
Mocking follows the same pattern as tests/test_mt5_connector.py: patch the
`mt5` name inside the module under test, using SimpleNamespace stand-ins for
MT5's return objects (only the attributes actually read are set).
"""

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import Mock, patch

import MetaTrader5 as mt5  # noqa: N813
import pytest

from core.models import AccountInfo, OrderType
from execution.models import OrderRequest, OrderResult, Position
from execution.mt5_broker import MT5Broker, _mt5_comment, _resolve_type_filling
from mt5.connector import MT5Connector


@pytest.fixture(autouse=True)
def _default_symbol_info() -> Iterator[None]:
    """Patches mt5.symbol_info() to a sane default for every test in this file.

    place_order()/close_position() now call it unconditionally (via
    _resolve_type_filling()), so every existing test needs SOME symbol_info()
    response to avoid a spurious RuntimeError. filling_mode=2 matches the
    real USTEC symbol (IOC only) confirmed against the live demo account.
    Tests that need a different filling_mode nest their own patch of
    mt5.symbol_info around this one, which wins for the duration of their
    `with` block.
    """
    with patch(
        "execution.mt5_broker.mt5.symbol_info",
        return_value=SimpleNamespace(filling_mode=2),
    ):
        yield


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
        broker = MT5Broker(connector, max_reconnect_attempts=1)

        with patch("execution.mt5_broker.activate_kill_switch") as mock_kill_switch:
            assert broker.connect() is False

        mock_kill_switch.assert_called_once()


class TestConnectReconnectWithBackoff:
    """Tests for MT5Broker.connect()'s T4 (Sprint 6c) retry/backoff/kill-switch behavior.

    time.sleep is always mocked -- these tests must not actually wait.
    """

    def test_succeeds_on_first_attempt_without_sleeping(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.connect.return_value = True
        broker = MT5Broker(connector)

        with patch("execution.mt5_broker.time.sleep") as mock_sleep:
            assert broker.connect() is True

        connector.connect.assert_called_once_with()
        mock_sleep.assert_not_called()

    def test_retries_with_exponential_backoff_then_succeeds(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.connect.side_effect = [False, False, True]
        broker = MT5Broker(
            connector, max_reconnect_attempts=5, initial_backoff_seconds=1.0, backoff_multiplier=2.0
        )

        with patch("execution.mt5_broker.time.sleep") as mock_sleep:
            assert broker.connect() is True

        assert connector.connect.call_count == 3
        assert mock_sleep.call_args_list == [((1.0,),), ((2.0,),)]

    def test_exhausting_all_attempts_activates_kill_switch_and_returns_false(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.connect.return_value = False
        broker = MT5Broker(connector, max_reconnect_attempts=3, initial_backoff_seconds=0.1)

        with (
            patch("execution.mt5_broker.time.sleep") as mock_sleep,
            patch("execution.mt5_broker.activate_kill_switch") as mock_kill_switch,
        ):
            result = broker.connect()

        assert result is False
        assert connector.connect.call_count == 3
        assert mock_sleep.call_count == 2  # backoff between attempts, not after the last one
        mock_kill_switch.assert_called_once()
        reason = mock_kill_switch.call_args[0][0]
        assert "3" in reason  # cites the attempt count in the kill-switch reason

    def test_does_not_activate_kill_switch_when_a_retry_succeeds(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.connect.side_effect = [False, True]
        broker = MT5Broker(connector)

        with (
            patch("execution.mt5_broker.time.sleep"),
            patch("execution.mt5_broker.activate_kill_switch") as mock_kill_switch,
        ):
            assert broker.connect() is True

        mock_kill_switch.assert_not_called()

    def test_constructor_rejects_non_positive_max_reconnect_attempts(self) -> None:
        with pytest.raises(ValueError, match="max_reconnect_attempts"):
            MT5Broker(max_reconnect_attempts=0)

    def test_constructor_rejects_non_positive_initial_backoff_seconds(self) -> None:
        with pytest.raises(ValueError, match="initial_backoff_seconds"):
            MT5Broker(initial_backoff_seconds=0.0)

    def test_constructor_rejects_negative_backoff_multiplier(self) -> None:
        with pytest.raises(ValueError, match="backoff_multiplier"):
            MT5Broker(backoff_multiplier=-1.0)

    def test_constructor_allows_zero_backoff_multiplier(self) -> None:
        MT5Broker(backoff_multiplier=0.0)  # constant (non-growing) backoff is a valid choice


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


class TestMt5CommentHelper:
    """Tests for _mt5_comment() (MT5's order-comment length limit).

    _MT5_COMMENT_MAX_LENGTH is 29, not the previously-assumed 31: that first
    fix still lost two real trades to the identical "(-2, 'Invalid comment
    argument')" order_send() failure, because 31 characters is itself past
    the real cutoff. 29 was empirically confirmed via mt5.order_check()
    (validate-only, no order placed) against the real demo account: a
    29-character comment is accepted (order_check reaches a business
    retcode, e.g. 10017 "Trade disabled", never the comment error); a
    30-character comment reproduces the exact production failure. See
    execution/mt5_broker.py's _MT5_COMMENT_MAX_LENGTH comment for the full
    detail of that probe.
    """

    def test_short_comment_passes_through_unchanged(self) -> None:
        assert _mt5_comment("short-id") == "short-id"

    def test_exactly_max_length_passes_through_unchanged(self) -> None:
        comment = "a" * 29
        assert _mt5_comment(comment) == comment

    def test_over_max_length_is_shortened_with_hash_suffix(self) -> None:
        comment = "a" * 40
        result = _mt5_comment(comment)

        assert len(result) == 29
        assert result.startswith("a" * 20 + "_")

    def test_shared_prefix_does_not_collide_after_shortening(self) -> None:
        base = "setup_midline_sweep_USTEC_M5_SELL_0c30fdc3_20260724_"
        comment_a = _mt5_comment(base + "143000_000000")
        comment_b = _mt5_comment(base + "143500_000001")

        assert len(comment_a) <= 29
        assert len(comment_b) <= 29
        assert comment_a != comment_b


class TestPlaceOrderCommentLength:
    """Tests for place_order() shortening OrderRequest.comment to fit MT5's real limit.

    Regression coverage for the bug where TradeManager.open_trade() passes the
    full TradeSetup.setup_id (commonly 60+ chars, e.g.
    "setup_midline_sweep_USTEC_M5_SELL_0c30fdc3_20260724_143000_000000") as
    OrderRequest.comment, which MT5's order_send() rejects outright
    (returns None, error (-2, 'Invalid "comment" argument')) once it exceeds
    29 characters (see _MT5_COMMENT_MAX_LENGTH) -- meaning every real order
    ever attempted through MT5Broker failed before reaching any other
    validation. A first fix (previous sprint) assumed the limit was 31 and
    shortened comments to exactly 31 chars; that value is ALSO past the real
    cutoff, so two more real signals crashed with the identical error even
    with that fix deployed. The setup_ids below are the literal comments
    from those two crash logs.
    """

    _LONG_SETUP_ID = "setup_midline_sweep_USTEC_M5_SELL_0c30fdc3_20260724_143000_000000"

    # The exact two setup_ids from the crash logs that reproduced the bug a
    # second time, with the (wrong) 31-char fix already deployed.
    _REAL_CRASHED_SETUP_IDS = (
        "setup_midline_sweep_USTEC_M5_BUY_2ff1f7a8_20260724_150500_000000",
        "setup_midline_sweep_USTEC_M5_SELL_7619658f_20260724_154000_000000",
    )

    def test_long_comment_sent_to_order_send_fits_mt5s_limit(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(
            symbol="USTEC",
            order_type=OrderType.BUY_MARKET,
            volume=0.1,
            price=100.0,
            comment=self._LONG_SETUP_ID,
        )
        assert len(self._LONG_SETUP_ID) > 29  # the bug only reproduces past MT5's real limit

        with (
            patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
            patch(
                "execution.mt5_broker.mt5.order_send", return_value=_order_send_result()
            ) as mock_send,
        ):
            broker.place_order(order)

        sent_comment = mock_send.call_args[0][0]["comment"]
        assert len(sent_comment) <= 29

    def test_real_crashed_setup_ids_fit_the_real_confirmed_limit_end_to_end(self) -> None:
        """Traces the two ACTUAL crashed setup_ids through the real place_order() path.

        Construct OrderRequest -> place_order() -> the dict handed to
        mt5.order_send() -- not _mt5_comment() called directly -- since that
        is the exact path that broke in production even after the first
        (31-char) fix was deployed, so it's the path that must be proven
        fixed. Independently confirmed against the real demo account via
        mt5.order_check() (see this task's investigation) that a 29-char
        comment is accepted and a 30-char one reproduces the exact crash;
        this test locks in that both real crashed comments now land at <= 29
        chars through this exact code path.
        """
        broker = MT5Broker()

        for setup_id in self._REAL_CRASHED_SETUP_IDS:
            order = OrderRequest(
                symbol="USTEC",
                order_type=OrderType.BUY_MARKET,
                volume=0.1,
                price=100.0,
                comment=setup_id,
            )

            with (
                patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
                patch(
                    "execution.mt5_broker.mt5.order_send", return_value=_order_send_result()
                ) as mock_send,
            ):
                broker.place_order(order)

            sent_comment = mock_send.call_args[0][0]["comment"]
            assert len(sent_comment) <= 29, (
                f"setup_id {setup_id!r} produced a comment of length "
                f"{len(sent_comment)} ({sent_comment!r}), still over MT5's real limit"
            )

    def test_short_comment_sent_unchanged(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(
            symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1, price=100.0, comment="short-id"
        )

        with (
            patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
            patch(
                "execution.mt5_broker.mt5.order_send", return_value=_order_send_result()
            ) as mock_send,
        ):
            broker.place_order(order)

        assert mock_send.call_args[0][0]["comment"] == "short-id"

    def test_full_setup_id_remains_recoverable_on_the_order_request(self) -> None:
        """Confirms OrderRequest.comment itself is never mutated.

        Only the outgoing MT5 request dict's comment is shortened, so
        run_live_demo.py's trade_events.log entries (which log
        setup.setup_id directly, not order.comment) keep the full
        identifier, joinable to execution_events.log via the shared
        order_id that both logs record.
        """
        broker = MT5Broker()
        order = OrderRequest(
            symbol="USTEC",
            order_type=OrderType.BUY_MARKET,
            volume=0.1,
            price=100.0,
            comment=self._LONG_SETUP_ID,
        )

        with (
            patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
            patch("execution.mt5_broker.mt5.order_send", return_value=_order_send_result(order=555)),
        ):
            broker.place_order(order)

        assert order.comment == self._LONG_SETUP_ID


class TestResolveTypeFilling:
    """Tests for _resolve_type_filling() (MT5's per-symbol filling-mode bitmask).

    Real risk, structurally identical to the comment-length bug: leaving
    order_send()'s type_filling unset lets the terminal default to a mode a
    symbol may not actually support, rejecting the whole request (retcode
    10030, "Unsupported filling mode"). Confirmed against the real demo
    account that USTEC's filling_mode bitmask is 2 (IOC only, no FOK bit).
    """

    def _symbol_info(self, filling_mode: int) -> SimpleNamespace:
        return SimpleNamespace(filling_mode=filling_mode)

    def test_ioc_only_bitmask_selects_ioc(self) -> None:

        with patch("execution.mt5_broker.mt5.symbol_info", return_value=self._symbol_info(2)):
            assert _resolve_type_filling("USTEC") == mt5.ORDER_FILLING_IOC

    def test_real_ustec_filling_mode_selects_ioc(self) -> None:
        """Locks in the specific real-world case.

        The bitmask value (2) was empirically confirmed via
        mt5.symbol_info("USTEC") against the real demo account.
        """
        with patch("execution.mt5_broker.mt5.symbol_info", return_value=self._symbol_info(2)):
            assert _resolve_type_filling("USTEC") == mt5.ORDER_FILLING_IOC == 1

    def test_fok_only_bitmask_selects_fok(self) -> None:

        with patch("execution.mt5_broker.mt5.symbol_info", return_value=self._symbol_info(1)):
            assert _resolve_type_filling("EURUSD") == mt5.ORDER_FILLING_FOK

    def test_both_fok_and_ioc_supported_prefers_ioc(self) -> None:

        with patch("execution.mt5_broker.mt5.symbol_info", return_value=self._symbol_info(3)):
            assert _resolve_type_filling("SOMESYMBOL") == mt5.ORDER_FILLING_IOC

    def test_neither_fok_nor_ioc_bit_set_falls_back_to_return(self) -> None:

        with patch("execution.mt5_broker.mt5.symbol_info", return_value=self._symbol_info(0)):
            assert _resolve_type_filling("SOMESYMBOL") == mt5.ORDER_FILLING_RETURN

    def test_boc_only_bit_falls_back_to_return(self) -> None:
        """A symbol advertising only BOC (bit 4) support must not be misread as FOK/IOC.

        BOC is a newer filling mode this codebase doesn't request.
        """
        with patch("execution.mt5_broker.mt5.symbol_info", return_value=self._symbol_info(4)):
            assert _resolve_type_filling("SOMESYMBOL") == mt5.ORDER_FILLING_RETURN

    def test_symbol_info_unavailable_raises(self) -> None:
        with patch("execution.mt5_broker.mt5.symbol_info", return_value=None):
            with pytest.raises(RuntimeError, match="symbol_info"):
                _resolve_type_filling("USTEC")


class TestPlaceOrderAndClosePositionSetTypeFilling:
    """Tests confirming place_order()/close_position() actually use _resolve_type_filling()."""

    def test_place_order_sends_the_resolved_type_filling(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1, price=100.0)

        with (
            patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
            patch(
                "execution.mt5_broker.mt5.symbol_info", return_value=SimpleNamespace(filling_mode=2)
            ),
            patch(
                "execution.mt5_broker.mt5.order_send", return_value=_order_send_result()
            ) as mock_send,
        ):
            broker.place_order(order)


        assert mock_send.call_args[0][0]["type_filling"] == mt5.ORDER_FILLING_IOC

    def test_place_order_selects_fok_when_only_fok_is_supported(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(symbol="EURUSD", order_type=OrderType.BUY_MARKET, volume=0.1, price=1.1)

        with (
            patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
            patch(
                "execution.mt5_broker.mt5.symbol_info", return_value=SimpleNamespace(filling_mode=1)
            ),
            patch(
                "execution.mt5_broker.mt5.order_send", return_value=_order_send_result()
            ) as mock_send,
        ):
            broker.place_order(order)


        assert mock_send.call_args[0][0]["type_filling"] == mt5.ORDER_FILLING_FOK

    def test_close_position_sends_the_resolved_type_filling(self) -> None:
        broker = MT5Broker()

        with (
            patch(
                "execution.mt5_broker.mt5.positions_get",
                return_value=[_mt5_position(ticket=777, type_=0, volume=0.3)],
            ),
            patch("execution.mt5_broker.mt5.symbol_info_tick", return_value=_tick(bid=100.0, ask=101.0)),
            patch(
                "execution.mt5_broker.mt5.symbol_info", return_value=SimpleNamespace(filling_mode=2)
            ),
            patch(
                "execution.mt5_broker.mt5.order_send", return_value=_order_send_result(order=999)
            ) as mock_send,
        ):
            broker.close_position("777")


        assert mock_send.call_args[0][0]["type_filling"] == mt5.ORDER_FILLING_IOC


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


class TestSlippageLogging:
    """Tests for T2 (Sprint 6c): MT5Broker logs realized slippage on every fill."""

    def test_place_order_logs_fill_with_pre_send_quote_as_intended(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1)

        with (
            patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
            patch("execution.mt5_broker.mt5.symbol_info_tick", return_value=_tick(bid=100.0, ask=101.0)),
            patch(
                "execution.mt5_broker.mt5.order_send",
                return_value=_order_send_result(order=555, price=101.3),
            ),
            patch("execution.mt5_broker.log_fill") as mock_log_fill,
        ):
            broker.place_order(order)

        mock_log_fill.assert_called_once()
        kwargs = mock_log_fill.call_args.kwargs
        assert kwargs["broker"] == "MT5Broker"
        assert kwargs["event"] == "open"
        assert kwargs["order_id"] == "555"
        assert kwargs["symbol"] == "USTEC"
        assert kwargs["order_type"] == OrderType.BUY_MARKET
        assert kwargs["intended_price"] == 101.0  # the pre-send ask quote
        assert kwargs["actual_price"] == 101.3  # what MT5 actually reported

    def test_place_order_does_not_log_a_fill_on_rejection(self) -> None:
        broker = MT5Broker()
        order = OrderRequest(symbol="USTEC", order_type=OrderType.BUY_MARKET, volume=0.1)
        rejected = _order_send_result(retcode=10019)  # TRADE_RETCODE_NO_MONEY

        with (
            patch("execution.mt5_broker.mt5.symbol_select", return_value=True),
            patch("execution.mt5_broker.mt5.symbol_info_tick", return_value=_tick()),
            patch("execution.mt5_broker.mt5.order_send", return_value=rejected),
            patch("execution.mt5_broker.log_fill") as mock_log_fill,
        ):
            broker.place_order(order)

        mock_log_fill.assert_not_called()

    def test_close_position_logs_fill_with_pre_send_quote_as_intended(self) -> None:
        broker = MT5Broker()

        with (
            patch(
                "execution.mt5_broker.mt5.positions_get",
                return_value=[_mt5_position(ticket=777, type_=0, volume=0.3)],
            ),
            patch("execution.mt5_broker.mt5.symbol_info_tick", return_value=_tick(bid=100.0, ask=101.0)),
            patch(
                "execution.mt5_broker.mt5.order_send",
                return_value=_order_send_result(order=999, price=99.8),
            ),
            patch("execution.mt5_broker.log_fill") as mock_log_fill,
        ):
            broker.close_position("777")

        mock_log_fill.assert_called_once()
        kwargs = mock_log_fill.call_args.kwargs
        assert kwargs["broker"] == "MT5Broker"
        assert kwargs["event"] == "close"
        assert kwargs["order_type"] == OrderType.SELL_MARKET  # closes a BUY position by selling
        assert kwargs["intended_price"] == 100.0  # the pre-send bid quote
        assert kwargs["actual_price"] == 99.8

    def test_close_position_does_not_log_a_fill_on_rejection(self) -> None:
        broker = MT5Broker()
        rejected = _order_send_result(retcode=10025)  # TRADE_RETCODE_NO_CHANGES

        with (
            patch("execution.mt5_broker.mt5.positions_get", return_value=[_mt5_position()]),
            patch("execution.mt5_broker.mt5.symbol_info_tick", return_value=_tick()),
            patch("execution.mt5_broker.mt5.order_send", return_value=rejected),
            patch("execution.mt5_broker.log_fill") as mock_log_fill,
        ):
            broker.close_position("777")

        mock_log_fill.assert_not_called()
