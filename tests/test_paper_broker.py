"""Unit tests for execution/paper_broker.py (PaperBroker).

MT5Connector is mocked throughout (Mock(spec=MT5Connector), fetch_recent_bars
returning fixed Bar objects) -- no real terminal connection is made.
State-file isolation and the corruption fail-open tests mirror
tests/test_daily_risk_tracker.py's pattern exactly: monkeypatch the module's
STATE_FILE constant to a tmp_path, then assert corrupt/missing/malformed
state resets to a fresh account without raising.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import execution.paper_broker as paper_broker_module
from core.models import Bar, OrderType, SymbolConstraints
from execution.models import OrderRequest
from execution.paper_broker import PaperBroker
from mt5.connector import MT5Connector


@pytest.fixture(autouse=True)
def _isolated_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paper_broker_module, "STATE_FILE", tmp_path / "paper_broker_state.json")


def _bar(open_: float = 29_000.0, close: float = 29_050.0, spread: float = 2.0) -> Bar:
    return Bar(
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        open=open_,
        high=max(open_, close) + 5,
        low=min(open_, close) - 5,
        close=close,
        volume=100.0,
        spread=spread,
    )


def _connector(bar: Bar | None = None) -> Mock:
    connector = Mock(spec=MT5Connector)
    connector.fetch_recent_bars.return_value = [bar if bar is not None else _bar()]
    return connector


def _market_order(symbol: str = "USTEC", volume: float = 0.1, buy: bool = True) -> OrderRequest:
    return OrderRequest(
        symbol=symbol,
        order_type=OrderType.BUY_MARKET if buy else OrderType.SELL_MARKET,
        volume=volume,
    )


class TestPlaceOrderMarket:
    """Tests for PaperBroker.place_order() with market orders."""

    def test_buy_fills_using_bar_open_spread_and_slippage(self) -> None:
        connector = _connector(_bar(open_=29_000.0, spread=2.0))
        broker = PaperBroker(connector=connector, slippage=0.5)

        result = broker.place_order(_market_order(buy=True))

        assert result.success is True
        # 29000.0 + half of 2.0 spread + 0.5 slippage
        assert result.price == pytest.approx(29_001.5)

    def test_sell_fills_using_bar_open_spread_and_slippage(self) -> None:
        connector = _connector(_bar(open_=29_000.0, spread=2.0))
        broker = PaperBroker(connector=connector, slippage=0.5)

        result = broker.place_order(_market_order(buy=False))

        assert result.success is True
        assert result.price == pytest.approx(28_998.5)

    def test_creates_an_open_position(self) -> None:
        connector = _connector(_bar(open_=29_000.0, spread=0.0))
        broker = PaperBroker(connector=connector, slippage=0.0)

        result = broker.place_order(_market_order(volume=0.2))

        positions = broker.get_open_positions()
        assert len(positions) == 1
        assert positions[0].id == result.order_id
        assert positions[0].symbol == "USTEC"
        assert positions[0].volume == 0.2
        assert positions[0].open_price == pytest.approx(29_000.0)

    def test_fetches_bars_for_the_ordered_symbol_and_configured_timeframe(self) -> None:
        connector = _connector()
        broker = PaperBroker(connector=connector, timeframe="M15")

        broker.place_order(_market_order(symbol="EURUSD"))

        connector.fetch_recent_bars.assert_called_with("EURUSD", "M15", count=1)

    def test_state_is_persisted_after_fill(self) -> None:
        connector = _connector(_bar(open_=29_000.0, spread=0.0))
        broker = PaperBroker(connector=connector, slippage=0.0)

        broker.place_order(_market_order())

        state = json.loads(paper_broker_module.STATE_FILE.read_text())
        assert len(state["orders"]) == 1
        assert len(state["positions"]) == 1
        order_data = next(iter(state["orders"].values()))
        assert order_data["status"] == "FILLED"


class TestPlaceOrderPending:
    """Tests for PaperBroker.place_order() with pending (limit/stop) orders."""

    def test_pending_order_stays_pending_and_creates_no_position(self) -> None:
        connector = _connector()
        broker = PaperBroker(connector=connector)
        order = OrderRequest(
            symbol="USTEC", order_type=OrderType.BUY_LIMIT, volume=0.1, price=28_500.0
        )

        result = broker.place_order(order)

        assert result.success is True
        assert broker.get_open_positions() == []
        connector.fetch_recent_bars.assert_not_called()

    def test_pending_order_persisted_as_pending(self) -> None:
        connector = _connector()
        broker = PaperBroker(connector=connector)
        order = OrderRequest(
            symbol="USTEC", order_type=OrderType.SELL_STOP, volume=0.1, price=28_500.0
        )

        broker.place_order(order)

        state = json.loads(paper_broker_module.STATE_FILE.read_text())
        order_data = next(iter(state["orders"].values()))
        assert order_data["status"] == "PENDING"


class TestCancelOrder:
    """Tests for PaperBroker.cancel_order()."""

    def test_cancel_pending_order_succeeds(self) -> None:
        connector = _connector()
        broker = PaperBroker(connector=connector)
        order = OrderRequest(
            symbol="USTEC", order_type=OrderType.BUY_LIMIT, volume=0.1, price=28_500.0
        )
        result = broker.place_order(order)

        assert broker.cancel_order(result.order_id) is True

        state = json.loads(paper_broker_module.STATE_FILE.read_text())
        order_data = next(iter(state["orders"].values()))
        assert order_data["status"] == "CANCELLED"

    def test_cancel_unknown_order_id_returns_false(self) -> None:
        broker = PaperBroker(connector=_connector())
        assert broker.cancel_order("does-not-exist") is False

    def test_cancel_already_filled_market_order_returns_false(self) -> None:
        connector = _connector(_bar(open_=29_000.0, spread=0.0))
        broker = PaperBroker(connector=connector, slippage=0.0)
        result = broker.place_order(_market_order())

        assert broker.cancel_order(result.order_id) is False


class TestClosePosition:
    """Tests for PaperBroker.close_position()."""

    def test_close_buy_position_uses_opposite_side_fill(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.fetch_recent_bars.side_effect = [
            [_bar(open_=29_000.0, spread=0.0)],  # opening fill bar
            [_bar(open_=29_100.0, spread=2.0)],  # closing fill bar
        ]
        broker = PaperBroker(connector=connector, slippage=0.5)
        open_result = broker.place_order(_market_order(volume=1.0, buy=True))

        close_result = broker.close_position(open_result.order_id)

        # Closing a BUY sells: SELL_MARKET fill = open - half spread - slippage
        assert close_result.success is True
        assert close_result.price == pytest.approx(29_100.0 - 1.0 - 0.5)
        assert close_result.position_id == open_result.order_id

    def test_close_sell_position_uses_opposite_side_fill(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.fetch_recent_bars.side_effect = [
            [_bar(open_=29_000.0, spread=0.0)],
            [_bar(open_=29_100.0, spread=2.0)],
        ]
        broker = PaperBroker(connector=connector, slippage=0.5)
        open_result = broker.place_order(_market_order(volume=1.0, buy=False))

        close_result = broker.close_position(open_result.order_id)

        # Closing a SELL buys: BUY_MARKET fill = open + half spread + slippage
        assert close_result.price == pytest.approx(29_100.0 + 1.0 + 0.5)

    def test_close_removes_the_position(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.fetch_recent_bars.side_effect = [
            [_bar(open_=29_000.0, spread=0.0)],
            [_bar(open_=29_100.0, spread=0.0)],
        ]
        broker = PaperBroker(connector=connector, slippage=0.0)
        open_result = broker.place_order(_market_order(volume=1.0))

        broker.close_position(open_result.order_id)

        assert broker.get_open_positions() == []

    def test_close_realizes_pnl_into_balance(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.fetch_recent_bars.side_effect = [
            [_bar(open_=29_000.0, spread=0.0)],
            [_bar(open_=29_150.0, spread=0.0)],
        ]
        broker = PaperBroker(connector=connector, initial_balance=10_000.0, slippage=0.0)
        open_result = broker.place_order(_market_order(volume=1.0, buy=True))

        broker.close_position(open_result.order_id)

        assert broker.get_account_info().balance == pytest.approx(10_150.0)

    def test_close_persists_a_new_filled_order_for_the_closing_leg(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.fetch_recent_bars.side_effect = [
            [_bar(open_=29_000.0, spread=0.0)],
            [_bar(open_=29_100.0, spread=0.0)],
        ]
        broker = PaperBroker(connector=connector, slippage=0.0)
        open_result = broker.place_order(_market_order(volume=1.0))

        close_result = broker.close_position(open_result.order_id)

        state = json.loads(paper_broker_module.STATE_FILE.read_text())
        assert len(state["orders"]) == 2
        closing_order_data = state["orders"][close_result.order_id]
        assert closing_order_data["status"] == "FILLED"
        # The original opening order is untouched (still FILLED, its own terminal status).
        opening_order_data = state["orders"][open_result.order_id]
        assert opening_order_data["status"] == "FILLED"

    def test_raises_for_unknown_position_id(self) -> None:
        broker = PaperBroker(connector=_connector())
        with pytest.raises(RuntimeError, match="No open paper position"):
            broker.close_position("does-not-exist")


class TestGetOpenPositions:
    """Tests for PaperBroker.get_open_positions() mark-to-market refresh."""

    def test_profit_reflects_current_close_price(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.fetch_recent_bars.side_effect = [
            [_bar(open_=29_000.0, spread=0.0)],  # fill bar
            [_bar(open_=29_100.0, close=29_150.0, spread=0.0)],  # mark-to-market bar
        ]
        broker = PaperBroker(connector=connector, slippage=0.0)
        broker.place_order(_market_order(volume=1.0, buy=True))

        positions = broker.get_open_positions()

        assert positions[0].current_price == pytest.approx(29_150.0)
        assert positions[0].profit == pytest.approx(150.0)  # (29150 - 29000) * 1.0

    def test_sell_position_profit_sign_is_inverted(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.fetch_recent_bars.side_effect = [
            [_bar(open_=29_000.0, spread=0.0)],
            [_bar(open_=28_900.0, close=28_850.0, spread=0.0)],
        ]
        broker = PaperBroker(connector=connector, slippage=0.0)
        broker.place_order(_market_order(volume=1.0, buy=False))

        positions = broker.get_open_positions()

        assert positions[0].profit == pytest.approx(150.0)  # (29000 - 28850) * 1.0

    def test_empty_when_no_positions(self) -> None:
        broker = PaperBroker(connector=_connector())
        assert broker.get_open_positions() == []


class TestGetAccountInfo:
    """Tests for PaperBroker.get_account_info()."""

    def test_equity_equals_balance_with_no_open_positions(self) -> None:
        broker = PaperBroker(connector=_connector(), initial_balance=10_000.0)
        info = broker.get_account_info()
        assert info.balance == 10_000.0
        assert info.equity == 10_000.0

    def test_equity_includes_floating_pnl(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.fetch_recent_bars.side_effect = [
            [_bar(open_=29_000.0, spread=0.0)],
            [_bar(open_=29_100.0, close=29_200.0, spread=0.0)],
        ]
        broker = PaperBroker(connector=connector, initial_balance=10_000.0, slippage=0.0)
        broker.place_order(_market_order(volume=1.0, buy=True))

        info = broker.get_account_info()

        assert info.balance == 10_000.0
        assert info.equity == pytest.approx(10_200.0)  # 10000 + (29200 - 29000) * 1.0


class TestGetSymbolConstraints:
    """Tests for PaperBroker.get_symbol_constraints()."""

    def test_delegates_to_connector_fetch_symbol_info(self) -> None:
        connector = _connector()
        expected = SymbolConstraints(
            symbol="USTEC",
            contract_size=1.0,
            tick_size=0.25,
            tick_value=0.25,
            volume_min=0.1,
            volume_max=50.0,
            volume_step=0.1,
        )
        connector.fetch_symbol_info.return_value = expected
        broker = PaperBroker(connector=connector)

        assert broker.get_symbol_constraints("USTEC") is expected
        connector.fetch_symbol_info.assert_called_once_with("USTEC")


class TestConnect:
    """Tests for PaperBroker.connect()."""

    def test_delegates_to_connector_connect(self) -> None:
        connector = _connector()
        connector.connect.return_value = True
        broker = PaperBroker(connector=connector)
        assert broker.connect() is True
        connector.connect.assert_called_once_with()


class TestStatePersistenceAcrossRestarts:
    """Simulated process-restart tests: a fresh PaperBroker() must load prior state."""

    def test_open_position_survives_broker_reconstruction(self) -> None:
        connector1 = _connector(_bar(open_=29_000.0, spread=0.0))
        broker1 = PaperBroker(connector=connector1, initial_balance=10_000.0, slippage=0.0)
        result = broker1.place_order(_market_order(volume=0.3))

        connector2 = _connector(_bar(open_=29_000.0, close=29_000.0, spread=0.0))
        broker2 = PaperBroker(connector=connector2, initial_balance=10_000.0, slippage=0.0)

        positions = broker2.get_open_positions()
        assert len(positions) == 1
        assert positions[0].id == result.order_id
        assert positions[0].volume == 0.3
        assert positions[0].open_price == pytest.approx(29_000.0)

    def test_balance_survives_broker_reconstruction(self) -> None:
        connector1 = _connector(_bar(open_=29_000.0, spread=0.0))
        broker1 = PaperBroker(connector=connector1, initial_balance=7_500.0, slippage=0.0)
        broker1.place_order(_market_order())

        broker2 = PaperBroker(connector=_connector(), initial_balance=10_000.0)
        # initial_balance=10_000.0 on broker2 must NOT override persisted 7_500.0
        assert broker2.get_account_info().balance == 7_500.0

    def test_pending_order_survives_and_can_be_cancelled_after_restart(self) -> None:
        connector1 = _connector()
        broker1 = PaperBroker(connector=connector1)
        order = OrderRequest(
            symbol="USTEC", order_type=OrderType.BUY_LIMIT, volume=0.1, price=28_500.0
        )
        result = broker1.place_order(order)

        broker2 = PaperBroker(connector=_connector())
        assert broker2.cancel_order(result.order_id) is True


class TestFailOpen:
    """Corrupted/malformed state must never raise -- always fail open to a fresh account."""

    def test_corrupt_json_resets_to_fresh_account(self) -> None:
        paper_broker_module.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        paper_broker_module.STATE_FILE.write_text("not valid json{{{")

        broker = PaperBroker(connector=_connector(), initial_balance=5_000.0)  # must not raise

        assert broker.get_account_info().balance == 5_000.0
        assert broker.get_open_positions() == []

    def test_missing_state_file_is_treated_as_fresh_account(self) -> None:
        assert not paper_broker_module.STATE_FILE.exists()
        broker = PaperBroker(connector=_connector(), initial_balance=5_000.0)
        assert broker.get_account_info().balance == 5_000.0

    def test_malformed_schema_resets_to_fresh_account(self) -> None:
        paper_broker_module.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        paper_broker_module.STATE_FILE.write_text(json.dumps({"balance": "not-a-number"}))

        broker = PaperBroker(connector=_connector(), initial_balance=5_000.0)  # must not raise

        assert broker.get_account_info().balance == 5_000.0
        assert broker.get_open_positions() == []

    def test_unrecognized_order_status_resets_to_fresh_account(self) -> None:
        paper_broker_module.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        paper_broker_module.STATE_FILE.write_text(
            json.dumps(
                {
                    "balance": 10_000.0,
                    "orders": {
                        "1": {
                            "order_id": "1",
                            "status": "NOT_A_REAL_STATUS",
                            "created_at": datetime.now(UTC).isoformat(),
                            "filled_at": None,
                            "fill_price": None,
                            "request": {
                                "symbol": "USTEC",
                                "order_type": "BUY_MARKET",
                                "volume": 0.1,
                                "price": None,
                                "stop_loss": None,
                                "take_profit": None,
                                "deviation": 10,
                                "comment": "",
                            },
                        }
                    },
                    "positions": {},
                }
            )
        )

        broker = PaperBroker(connector=_connector(), initial_balance=5_000.0)  # must not raise

        assert broker.get_account_info().balance == 5_000.0

    def test_write_failure_does_not_raise(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        blocked_parent = tmp_path / "blocked"
        blocked_parent.write_text("a file, not a directory")  # mkdir() on this path will raise
        monkeypatch.setattr(paper_broker_module, "STATE_FILE", blocked_parent / "state.json")
        connector = _connector(_bar(open_=29_000.0, spread=0.0))
        broker = PaperBroker(connector=connector, slippage=0.0)

        broker.place_order(_market_order())  # must not raise despite the failed persist


class TestSlippageLogging:
    """Tests for T2 (Sprint 6c): PaperBroker logs realized slippage on every fill."""

    def test_place_order_logs_fill_with_reference_price_as_intended(self) -> None:
        connector = _connector(_bar(open_=29_000.0, spread=2.0))
        broker = PaperBroker(connector=connector, slippage=0.5)

        with patch("execution.paper_broker.log_fill") as mock_log_fill:
            result = broker.place_order(_market_order(volume=1.0, buy=True))

        mock_log_fill.assert_called_once()
        kwargs = mock_log_fill.call_args.kwargs
        assert kwargs["broker"] == "PaperBroker"
        assert kwargs["event"] == "open"
        assert kwargs["order_id"] == result.order_id
        assert kwargs["symbol"] == "USTEC"
        assert kwargs["order_type"] == OrderType.BUY_MARKET
        assert kwargs["volume"] == 1.0
        # reference price is the bar's open, BEFORE simulated spread/slippage.
        assert kwargs["intended_price"] == pytest.approx(29_000.0)
        assert kwargs["actual_price"] == result.price

    def test_close_position_logs_fill_with_reference_price_as_intended(self) -> None:
        connector = Mock(spec=MT5Connector)
        connector.fetch_recent_bars.side_effect = [
            [_bar(open_=29_000.0, spread=0.0)],
            [_bar(open_=29_100.0, spread=2.0)],
        ]
        broker = PaperBroker(connector=connector, slippage=0.5)
        open_result = broker.place_order(_market_order(volume=1.0, buy=True))

        with patch("execution.paper_broker.log_fill") as mock_log_fill:
            close_result = broker.close_position(open_result.order_id)

        mock_log_fill.assert_called_once()
        kwargs = mock_log_fill.call_args.kwargs
        assert kwargs["broker"] == "PaperBroker"
        assert kwargs["event"] == "close"
        assert kwargs["order_type"] == OrderType.SELL_MARKET  # closes a BUY by selling
        assert kwargs["intended_price"] == pytest.approx(29_100.0)
        assert kwargs["actual_price"] == close_result.price

    def test_pending_order_does_not_log_a_fill(self) -> None:
        """Pending orders (Sprint 2 scope) are stored, not filled -- no fill event exists to log."""
        connector = _connector()
        broker = PaperBroker(connector=connector)
        order = OrderRequest(symbol="USTEC", order_type=OrderType.BUY_LIMIT, volume=0.1, price=28_500.0)

        with patch("execution.paper_broker.log_fill") as mock_log_fill:
            broker.place_order(order)

        mock_log_fill.assert_not_called()
