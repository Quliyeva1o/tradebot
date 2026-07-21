"""Full trade-lifecycle tests for execution/trade_manager.py.

Uses PaperBroker as the IBroker implementation (MT5Connector mocked
throughout -- no real terminal connection, no real order ever sent).

Covers: open -> hold several bars -> close on SL, and separately
open -> hold -> close on TP, plus the same-bar SL/TP tie-break, the
no-double-open guard, and the manual-close path.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

import execution.paper_broker as paper_broker_module
from core.models import Bar, SignalDirection, Timeframe
from execution.models import TradeManagerAction
from execution.order import OrderStatus
from execution.paper_broker import PaperBroker
from execution.stop_engine import FixedStopEngine
from execution.take_profit_engine import FixedTakeProfitEngine
from execution.trade_manager import TradeManager
from mt5.connector import MT5Connector
from strategy.models import TradeSetup
from strategy.risk_reward import resolve_stop_and_target


@pytest.fixture(autouse=True)
def _isolated_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paper_broker_module, "STATE_FILE", tmp_path / "paper_broker_state.json")


def _fill_bar(open_: float, spread: float = 0.0) -> Bar:
    """A bar shaped for use as a PaperBroker fill reference (open/spread matter)."""
    return Bar(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        open=open_,
        high=open_ + 1,
        low=open_ - 1,
        close=open_,
        volume=100.0,
        spread=spread,
    )


def _price_bar(low: float, high: float) -> Bar:
    """A bar shaped for use as a TradeManager.on_new_bar() price check (high/low matter)."""
    return Bar(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        open=(low + high) / 2,
        high=high,
        low=low,
        close=(low + high) / 2,
        volume=100.0,
        spread=0.0,
    )


def _setup(
    direction: SignalDirection = SignalDirection.BUY,
    entry: float = 29_000.0,
    stop_loss: float = 28_900.0,
    take_profit: float = 29_200.0,
) -> TradeSetup:
    return TradeSetup(
        setup_id="setup-1",
        symbol="USTEC",
        timeframe=Timeframe.M5,
        direction=direction,
        entry_zone=(entry, entry),
        stop_zone=(stop_loss, stop_loss),
        target_zone=(take_profit, take_profit),
        confidence_score=1.0,
        confluence=[],
        trigger_reason="test",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        strategy_name="TestStrategy",
    )


def _broker(*fill_bars: Bar) -> PaperBroker:
    """A PaperBroker whose MT5Connector yields fill_bars in order.

    One bar per fetch_recent_bars() call (first call = opening fill, second
    = closing fill). Any call beyond len(fill_bars) -- e.g. an incidental
    get_open_positions() mark-to-market refresh while a trade is still open
    -- repeats the last bar given, so tests don't need to account for every
    such call explicitly.
    """
    connector = Mock(spec=MT5Connector)

    def _bars_forever() -> Iterator[list[Bar]]:
        last = fill_bars[-1]
        for bar in fill_bars:
            last = bar
            yield [bar]
        while True:
            yield [last]

    connector.fetch_recent_bars.side_effect = _bars_forever()
    return PaperBroker(connector=connector, slippage=0.0)


class TestOpenTrade:
    """Tests for TradeManager.open_trade()."""

    def test_open_trade_returns_a_filled_order(self) -> None:
        broker = _broker(_fill_bar(29_000.0))
        manager = TradeManager(volume=0.2)

        order = manager.open_trade(_setup(), broker)

        assert order.status is OrderStatus.FILLED
        assert order.fill_price == pytest.approx(29_000.0)
        assert order.request.volume == 0.2
        assert manager.has_open_trade is True

    def test_open_trade_opens_a_position_on_the_broker(self) -> None:
        broker = _broker(_fill_bar(29_000.0))
        manager = TradeManager(volume=0.1)

        manager.open_trade(_setup(), broker)

        positions = broker.get_open_positions()
        assert len(positions) == 1
        assert positions[0].stop_loss == pytest.approx(28_900.0)
        assert positions[0].take_profit == pytest.approx(29_200.0)

    def test_opening_a_second_trade_while_one_is_open_raises(self) -> None:
        broker = _broker(_fill_bar(29_000.0))
        manager = TradeManager()
        manager.open_trade(_setup(), broker)

        with pytest.raises(RuntimeError, match="already has an open trade"):
            manager.open_trade(_setup(), broker)


class TestOpenTradeEngines:
    """Sprint 4: open_trade()'s stop_engine/take_profit_engine wiring.

    Confirms default engines (FixedStopEngine/FixedTakeProfitEngine)
    reproduce the exact pre-Sprint-4 behavior (calling
    strategy.risk_reward.resolve_stop_and_target() directly), and that a
    swapped-in custom engine genuinely changes the levels TradeManager
    tracks -- proving the interface is not just superficial wiring.
    """

    def test_default_engines_match_direct_resolve_stop_and_target_call(self) -> None:
        setup = _setup(stop_loss=28_900.0, take_profit=29_200.0)
        expected_stop_loss, expected_take_profit = resolve_stop_and_target(setup)
        broker = _broker(_fill_bar(29_000.0))
        manager = TradeManager()

        manager.open_trade(setup, broker)

        positions = broker.get_open_positions()
        assert positions[0].stop_loss == expected_stop_loss
        assert positions[0].take_profit == expected_take_profit

    def test_explicit_fixed_engines_match_default_engines(self) -> None:
        setup = _setup(stop_loss=28_900.0, take_profit=29_200.0)

        broker_default = _broker(_fill_bar(29_000.0))
        manager_default = TradeManager()
        manager_default.open_trade(setup, broker_default)

        broker_explicit = _broker(_fill_bar(29_000.0))
        manager_explicit = TradeManager()
        manager_explicit.open_trade(
            setup, broker_explicit, FixedStopEngine(), FixedTakeProfitEngine()
        )

        default_position = broker_default.get_open_positions()[0]
        explicit_position = broker_explicit.get_open_positions()[0]
        assert default_position.stop_loss == explicit_position.stop_loss
        assert default_position.take_profit == explicit_position.take_profit

    def test_open_trade_returns_identical_order_with_default_vs_explicit_fixed_engines(self) -> None:
        setup = _setup(stop_loss=28_900.0, take_profit=29_200.0)

        order_default = TradeManager().open_trade(setup, _broker(_fill_bar(29_000.0)))
        order_explicit = TradeManager().open_trade(
            setup, _broker(_fill_bar(29_000.0)), FixedStopEngine(), FixedTakeProfitEngine()
        )

        assert order_default.fill_price == order_explicit.fill_price
        assert order_default.request.stop_loss == order_explicit.request.stop_loss
        assert order_default.request.take_profit == order_explicit.request.take_profit
        assert order_default.status == order_explicit.status

    def test_custom_stop_engine_overrides_the_resolved_stop_level(self) -> None:
        class AlwaysStop28000:
            def resolve_stop(self, setup: TradeSetup) -> float:
                return 28_000.0

        setup = _setup(stop_loss=28_900.0, take_profit=29_200.0)
        broker = _broker(_fill_bar(29_000.0))
        manager = TradeManager()

        manager.open_trade(setup, broker, stop_engine=AlwaysStop28000())

        assert broker.get_open_positions()[0].stop_loss == 28_000.0

    def test_custom_take_profit_engine_overrides_the_resolved_target_level(self) -> None:
        class AlwaysTarget30000:
            def resolve_take_profit(self, setup: TradeSetup) -> float:
                return 30_000.0

        setup = _setup(stop_loss=28_900.0, take_profit=29_200.0)
        broker = _broker(_fill_bar(29_000.0))
        manager = TradeManager()

        manager.open_trade(setup, broker, take_profit_engine=AlwaysTarget30000())

        assert broker.get_open_positions()[0].take_profit == 30_000.0


class TestOnNewBarHoldAndCloseSL:
    """Full lifecycle: open -> hold several bars -> close on SL."""

    def test_holds_while_price_stays_between_sl_and_tp(self) -> None:
        broker = _broker(_fill_bar(29_000.0))
        manager = TradeManager()
        manager.open_trade(_setup(stop_loss=28_900.0, take_profit=29_200.0), broker)

        for _ in range(3):
            action = manager.on_new_bar(_price_bar(low=28_950.0, high=29_050.0))
            assert action is TradeManagerAction.HELD
        assert manager.has_open_trade is True

    def test_closes_on_sl_hit_and_position_is_removed(self) -> None:
        broker = _broker(_fill_bar(29_000.0), _fill_bar(28_895.0))
        manager = TradeManager()
        manager.open_trade(_setup(stop_loss=28_900.0, take_profit=29_200.0), broker)

        for _ in range(2):
            assert manager.on_new_bar(_price_bar(low=28_950.0, high=29_050.0)) is TradeManagerAction.HELD

        action = manager.on_new_bar(_price_bar(low=28_850.0, high=28_950.0))

        assert action is TradeManagerAction.CLOSED_SL
        assert manager.has_open_trade is False
        assert broker.get_open_positions() == []

    def test_further_bars_after_close_are_a_no_op(self) -> None:
        broker = _broker(_fill_bar(29_000.0), _fill_bar(28_895.0))
        manager = TradeManager()
        manager.open_trade(_setup(stop_loss=28_900.0, take_profit=29_200.0), broker)
        manager.on_new_bar(_price_bar(low=28_850.0, high=28_950.0))  # closes on SL

        action = manager.on_new_bar(_price_bar(low=28_000.0, high=30_000.0))

        assert action is TradeManagerAction.HELD


class TestOnNewBarHoldAndCloseTP:
    """Full lifecycle: open -> hold -> close on TP."""

    def test_holds_then_closes_on_tp_hit(self) -> None:
        broker = _broker(_fill_bar(29_000.0), _fill_bar(29_205.0))
        manager = TradeManager()
        manager.open_trade(_setup(stop_loss=28_900.0, take_profit=29_200.0), broker)

        assert manager.on_new_bar(_price_bar(low=28_980.0, high=29_050.0)) is TradeManagerAction.HELD
        assert manager.on_new_bar(_price_bar(low=29_020.0, high=29_150.0)) is TradeManagerAction.HELD

        action = manager.on_new_bar(_price_bar(low=29_150.0, high=29_250.0))

        assert action is TradeManagerAction.CLOSED_TP
        assert manager.has_open_trade is False
        assert broker.get_open_positions() == []

    def test_sell_setup_closes_on_tp_when_price_drops(self) -> None:
        broker = _broker(_fill_bar(29_000.0), _fill_bar(28_795.0))
        manager = TradeManager()
        manager.open_trade(
            _setup(direction=SignalDirection.SELL, entry=29_000.0, stop_loss=29_100.0, take_profit=28_800.0),
            broker,
        )

        action = manager.on_new_bar(_price_bar(low=28_750.0, high=28_950.0))

        assert action is TradeManagerAction.CLOSED_TP


class TestSameBarConflict:
    """Same-bar SL/TP conflict: SL must take precedence (mirrors BacktestEngine.run())."""

    def test_sl_takes_precedence_when_both_touched_same_bar(self) -> None:
        broker = _broker(_fill_bar(29_000.0), _fill_bar(28_895.0))
        manager = TradeManager()
        manager.open_trade(_setup(stop_loss=28_900.0, take_profit=29_200.0), broker)

        action = manager.on_new_bar(_price_bar(low=28_800.0, high=29_300.0))

        assert action is TradeManagerAction.CLOSED_SL


class TestOnNewBarNoOpenTrade:
    """on_new_bar() must be a safe no-op with nothing open."""

    def test_returns_held_and_makes_no_broker_calls(self) -> None:
        broker = Mock()
        manager = TradeManager()

        action = manager.on_new_bar(_price_bar(low=1.0, high=2.0))

        assert action is TradeManagerAction.HELD
        broker.close_position.assert_not_called()


class TestCloseTrade:
    """Tests for TradeManager.close_trade() (manual close)."""

    def test_manual_close_returns_closed_manual_and_removes_position(self) -> None:
        broker = _broker(_fill_bar(29_000.0), _fill_bar(29_010.0))
        manager = TradeManager()
        manager.open_trade(_setup(), broker)

        action = manager.close_trade()

        assert action is TradeManagerAction.CLOSED_MANUAL
        assert manager.has_open_trade is False
        assert broker.get_open_positions() == []

    def test_manual_close_with_nothing_open_returns_held(self) -> None:
        manager = TradeManager()
        assert manager.close_trade() is TradeManagerAction.HELD


class TestOpenTradeRejection:
    """A broker-rejected entry must not be tracked as an open trade."""

    def test_rejected_entry_returns_rejected_order_and_tracks_nothing(self) -> None:
        broker = Mock()
        broker.place_order.return_value = Mock(success=False, order_id="x", comment="no money")
        manager = TradeManager()

        order = manager.open_trade(_setup(), broker)

        assert order.status is OrderStatus.REJECTED
        assert manager.has_open_trade is False
