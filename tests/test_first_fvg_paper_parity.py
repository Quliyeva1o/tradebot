"""Tests for scripts/first_fvg_paper_parity.py -- matching paper trades to their backtest twins."""

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock

from core.models import Bar, OrderType, SymbolConstraints
from execution.models import OrderRequest
from execution.paper_broker import PaperBroker
from mt5.connector import MT5Connector
from scripts.first_fvg_paper_parity import PaperTrade, compare, comparison_start, paper_trades_from_state
from scripts.first_fvg_window_backtest import FvgTrade

SID = "setup_fvg_window_NDX100_20260914_BUY"
T_ENTRY = datetime(2026, 9, 14, 14, 31, tzinfo=UTC)
T_EXIT = datetime(2026, 9, 14, 14, 40, tzinfo=UTC)


def _bt(entry: float = 100.0, exit_price: float = 90.0, reason: str = "SL", sid: str = SID) -> FvgTrade:
    return FvgTrade(day=date(2026, 9, 14), setup_id=sid, direction="LONG", entry_time=T_ENTRY, entry=entry,
                    stop=90.0, target=130.0, exit_time=T_EXIT, exit_price=exit_price, reason=reason,
                    r_gross=-1.0, r_net=-1.1)


def _paper(entry: float = 100.0, exit_price: float | None = 90.0, sid: str = SID) -> PaperTrade:
    return PaperTrade(setup_id=sid, entry_time=T_ENTRY, entry=entry, stop=90.0, target=130.0,
                      exit_time=T_EXIT if exit_price is not None else None, exit_price=exit_price)


class TestCompare:
    def test_identical_fills_match(self) -> None:
        [row] = compare([_bt()], [_paper()])
        assert row.status == "match"

    def test_a_different_entry_price_is_reported(self) -> None:
        [row] = compare([_bt()], [_paper(entry=101.5)])
        assert row.status == "differs"
        assert "entry 100.00 vs 101.50" in row.detail

    def test_backtest_trade_the_paper_bot_never_took(self) -> None:
        [row] = compare([_bt()], [])
        assert row.status == "missing_in_paper"

    def test_paper_trade_the_backtest_does_not_have(self) -> None:
        [row] = compare([], [_paper()])
        assert row.status == "missing_in_backtest"

    def test_paper_trade_still_open_is_not_called_a_mismatch(self) -> None:
        [row] = compare([], [_paper(exit_price=None)])
        assert row.status == "open"


class TestComparisonStart:
    """Setups from before the bot existed must not be reported as trades it missed."""

    def test_explicit_since_is_used(self, tmp_path: Path) -> None:
        start = comparison_start(tmp_path / "missing.json", first_covered=date(2026, 8, 1), since=date(2026, 9, 15))
        assert start == date(2026, 9, 15)

    def test_since_before_the_fetched_bars_is_clamped_to_them(self, tmp_path: Path) -> None:
        start = comparison_start(tmp_path / "missing.json", first_covered=date(2026, 8, 1), since=date(2026, 7, 1))
        assert start == date(2026, 8, 1)

    def test_defaults_to_the_setup_date_of_the_bots_first_order(self, tmp_path: Path) -> None:
        state = tmp_path / "paper_broker_state_fvg_window_ndx100.json"
        broker = PaperBroker(connector=Mock(spec=MT5Connector), timeframe="M1", level_fills=True, state_file=state)
        for day in (15, 14):  # placed out of order: the earliest setup date must win
            broker.place_order(OrderRequest(
                symbol="NDX100", order_type=OrderType.BUY_LIMIT, volume=1.0, price=100.0, stop_loss=90.0,
                take_profit=130.0, comment=f"setup_fvg_window_NDX100_202609{day}_BUY",
                valid_from=datetime(2026, 9, day, 14, 30, tzinfo=UTC),
                expires_at=datetime(2026, 9, day + 1, 4, 0, tzinfo=UTC)))

        assert comparison_start(state, first_covered=date(2026, 8, 1), since=None) == date(2026, 9, 14)

    def test_nothing_to_compare_before_the_bot_has_ordered(self, tmp_path: Path) -> None:
        assert comparison_start(tmp_path / "missing.json", first_covered=date(2026, 8, 1), since=None) is None


def test_reads_filled_and_closed_trades_from_a_real_paper_broker_state(tmp_path: Path) -> None:
    connector = Mock(spec=MT5Connector)
    connector.fetch_symbol_info.return_value = SymbolConstraints(
        symbol="NDX100", contract_size=1.0, tick_size=1.0, tick_value=1.0,
        volume_min=0.01, volume_max=100.0, volume_step=0.01)
    connector.fetch_recent_bars.return_value = [
        Bar(timestamp=T_ENTRY, open=103, high=104, low=99.5, close=101, volume=1.0),   # limit 100 fills
        Bar(timestamp=T_EXIT, open=95, high=96, low=89, close=91, volume=1.0),         # stop 90
    ]
    state = tmp_path / "paper_broker_state_fvg_window_ndx100.json"
    broker = PaperBroker(connector=connector, timeframe="M1", level_fills=True, state_file=state)
    broker.place_order(OrderRequest(
        symbol="NDX100", order_type=OrderType.BUY_LIMIT, volume=1.0, price=100.0, stop_loss=90.0,
        take_profit=130.0, comment=SID, valid_from=datetime(2026, 9, 14, 14, 30, tzinfo=UTC),
        expires_at=datetime(2026, 9, 15, 4, 0, tzinfo=UTC)))
    broker.get_open_positions()

    assert paper_trades_from_state(state) == [_paper()]
