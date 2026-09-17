"""The resting reverse order must follow the bot's open trade: placed while it is open, cancelled once
it is gone, never duplicated, never placed for the reverse trade itself."""

from datetime import UTC, datetime

import pytest

from core.models import OrderType, SymbolConstraints
from execution.models import OrderRequest, OrderResult, PendingOrder, Position
from execution.mt5_broker import _MT5_COMMENT_MAX_LENGTH
from execution.stop_and_reverse import (
    is_reverse,
    plan_reverse_order,
    reverse_comment,
    reverse_tag,
    sync_reverse_order,
)

BREAKOUT_TAG = "setup_nasdaq_orb_m1"
SWEEP_TAG = "setup_xauusd_orb"


def _position(ticket: str = "12884987", order_type: OrderType = OrderType.BUY_MARKET,
              open_price: float = 4354.60, stop: float | None = 4340.31, target: float = 4411.76,
              comment: str = "setup_nasdaq_orb_m1__9f089d26", volume: float = 0.16) -> Position:
    return Position(id=ticket, symbol="XAUUSD", order_type=order_type, volume=volume,
                    open_price=open_price, current_price=open_price, stop_loss=stop,
                    take_profit=target, timestamp=datetime(2026, 9, 16, 15, 0, tzinfo=UTC),
                    comment=comment)


class FakeBroker:
    def __init__(self, pending: list[PendingOrder] | None = None, accept: bool = True) -> None:
        self.pending = list(pending or [])
        self.accept = accept
        self.placed: list[OrderRequest] = []
        self.canceled: list[str] = []

    def get_pending_orders(self, symbol: str) -> list[PendingOrder]:
        return [o for o in self.pending if o.symbol == symbol]

    def get_symbol_constraints(self, symbol: str) -> SymbolConstraints:
        return SymbolConstraints(symbol=symbol, contract_size=100.0, tick_size=0.01, tick_value=1.0,
                                 volume_min=0.01, volume_max=100.0, volume_step=0.01)

    def cancel_order(self, order_id: str) -> bool:
        self.canceled.append(order_id)
        self.pending = [o for o in self.pending if o.id != order_id]
        return True

    def place_order(self, order: OrderRequest) -> OrderResult:
        self.placed.append(order)
        if not self.accept:
            return OrderResult(success=False, retcode=10015, comment="Invalid price")
        return OrderResult(success=True, order_id="900", position_id="900", retcode=10008)


class Events(list):
    def __call__(self, event: str, **fields: object) -> None:
        self.append((event, fields))

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self]


def _resting(position_id: str = "12884987", order_id: str = "501", tag: str = BREAKOUT_TAG) -> PendingOrder:
    return PendingOrder(id=order_id, symbol="XAUUSD", order_type=OrderType.SELL_STOP, volume=0.16,
                        price=4340.31, stop_loss=4354.60, take_profit=4333.17,
                        comment=reverse_comment(tag, position_id))


def test_a_long_trade_is_reversed_by_a_sell_stop_at_its_stop_with_the_stop_back_at_its_entry() -> None:
    order = plan_reverse_order(_position(), 0.5, 0.01, BREAKOUT_TAG)
    assert order is not None
    assert (order.order_type, order.price, order.stop_loss, order.volume) == (
        OrderType.SELL_STOP, 4340.31, 4354.60, 0.16)
    assert order.take_profit == pytest.approx(4340.31 - 0.5 * 14.29, abs=0.01)  # rounded to the tick
    assert round(order.take_profit / 0.01, 6).is_integer()


def test_a_short_trade_is_reversed_by_a_buy_stop() -> None:
    order = plan_reverse_order(_position(order_type=OrderType.SELL_MARKET, open_price=25391.66,
                                         stop=25482.74, target=25209.50), 0.5, 0.01, SWEEP_TAG)
    assert order is not None
    assert (order.order_type, order.price, order.stop_loss) == (OrderType.BUY_STOP, 25482.74, 25391.66)
    assert order.take_profit == pytest.approx(25482.74 + 0.5 * 91.08)


def test_a_trade_without_a_stop_cannot_be_reversed() -> None:
    assert plan_reverse_order(_position(stop=None), 0.5, 0.01, BREAKOUT_TAG) is None


@pytest.mark.parametrize("tag", [BREAKOUT_TAG, SWEEP_TAG])
def test_the_comment_fits_mt5_and_still_belongs_to_the_bot(tag: str) -> None:
    comment = reverse_comment(tag, "12884987")
    assert len(comment) <= _MT5_COMMENT_MAX_LENGTH
    assert comment.startswith(tag) and is_reverse(comment, tag)


def test_real_original_comments_are_not_mistaken_for_reverse_trades() -> None:
    # comments the account's real deals carried on 2026-09-15/16
    assert not is_reverse("setup_nasdaq_orb_m1__9f089d26", BREAKOUT_TAG)
    assert not is_reverse("setup_xauusd_orb_rev_d929727e", SWEEP_TAG)  # the sweep is "reversal"
    assert reverse_tag(SWEEP_TAG) == "setup_xauusd_orb_sar"


def test_an_open_trade_without_a_resting_order_gets_one() -> None:
    broker, events = FakeBroker(), Events()
    sync_reverse_order(broker, "XAUUSD", BREAKOUT_TAG, [_position()], 0.5, events)
    assert [o.comment for o in broker.placed] == [reverse_comment(BREAKOUT_TAG, "12884987")]
    assert events.names == ["reverse_order_placed"]


def test_a_matching_resting_order_is_left_alone() -> None:
    broker, events = FakeBroker([_resting()]), Events()
    sync_reverse_order(broker, "XAUUSD", BREAKOUT_TAG, [_position()], 0.5, events)
    assert (broker.placed, broker.canceled, events.names) == ([], [], [])


def test_a_resting_order_for_an_older_trade_is_replaced() -> None:
    broker, events = FakeBroker([_resting(position_id="12734468", order_id="400")]), Events()
    sync_reverse_order(broker, "XAUUSD", BREAKOUT_TAG, [_position()], 0.5, events)
    assert broker.canceled == ["400"]
    assert len(broker.placed) == 1
    assert events.names == ["reverse_order_canceled", "reverse_order_placed"]


def test_once_the_trade_is_gone_the_resting_order_is_cancelled() -> None:
    broker, events = FakeBroker([_resting()]), Events()
    sync_reverse_order(broker, "XAUUSD", BREAKOUT_TAG, [], 0.5, events)  # it reached its target
    assert (broker.canceled, broker.placed) == (["501"], [])


def test_the_open_reverse_trade_is_never_reversed_again() -> None:
    reverse = _position(ticket="12890000", order_type=OrderType.SELL_MARKET, open_price=4340.31,
                        stop=4354.60, target=4333.17, comment=reverse_comment(BREAKOUT_TAG, "12884987"))
    broker, events = FakeBroker(), Events()
    sync_reverse_order(broker, "XAUUSD", BREAKOUT_TAG, [reverse], 0.5, events)
    assert (broker.placed, broker.canceled, events.names) == ([], [], [])


def test_while_halted_nothing_rests() -> None:
    broker, events = FakeBroker([_resting()]), Events()
    sync_reverse_order(broker, "XAUUSD", BREAKOUT_TAG, [_position()], 0.5, events, halted=True)
    assert (broker.canceled, broker.placed) == (["501"], [])


def test_another_bots_resting_orders_are_not_touched() -> None:
    foreign = PendingOrder(id="77", symbol="XAUUSD", order_type=OrderType.SELL_STOP, volume=1.0,
                           price=4300.0, comment="setup_xauusd_orb_sar12884987")
    broker, events = FakeBroker([foreign]), Events()
    sync_reverse_order(broker, "XAUUSD", BREAKOUT_TAG, [], 0.5, events)
    assert broker.canceled == []


def test_a_rejected_order_is_logged_and_retried_at_the_next_poll() -> None:
    broker, events = FakeBroker(accept=False), Events()
    sync_reverse_order(broker, "XAUUSD", BREAKOUT_TAG, [_position()], 0.5, events)
    assert events.names == ["reverse_order_rejected"]
    assert events[0][1]["retcode"] == 10015
    broker.accept = True
    sync_reverse_order(broker, "XAUUSD", BREAKOUT_TAG, [_position()], 0.5, events)
    assert events.names[-1] == "reverse_order_placed"
