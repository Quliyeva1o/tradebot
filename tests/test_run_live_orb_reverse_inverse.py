"""The ORB runners wire --reverse-on-stop and --inverse in: the reverse order follows each poll, and
an inverse bot opens the mirror of the signal it found."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import run_live_nasdaq_orb
import run_live_xauusd_orb
from core.models import Bar, OrderType, SignalDirection, SymbolConstraints, Timeframe
from execution.models import OrderRequest, OrderResult, PendingOrder, Position
from execution.order import OrderStatus
from execution.stop_and_reverse import reverse_comment
from execution.trade_manager import TradeManager
from strategy.models import TradeSetup

RUNNERS = [
    pytest.param(run_live_nasdaq_orb, ["--symbol", "XAUUSD", "--tp-r", "4.0"], id="breakout"),
    pytest.param(run_live_xauusd_orb, ["--symbol", "GER40"], id="sweep"),
]
NOW = datetime(2026, 9, 16, 15, 10, tzinfo=UTC)


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list:
    seen: list = []
    for runner in (run_live_nasdaq_orb, run_live_xauusd_orb):
        monkeypatch.setattr(runner, "_log_trade_event", lambda event, **f: seen.append((event, f)))
        monkeypatch.setattr(runner, "_log_sizing", lambda *_a, **_k: None)
    return seen


@pytest.mark.parametrize("runner, base", RUNNERS)
def test_reverse_on_stop_is_refused_for_paper(runner, base) -> None:
    with pytest.raises(SystemExit):
        runner.parse_args([*base, "--paper", "--reverse-on-stop", "0.5"])


@pytest.mark.parametrize("runner, base", RUNNERS)
def test_the_launchers_flags_parse(runner, base) -> None:
    demo = runner.parse_args([*base, "--reverse-on-stop", "0.5"])
    paper = runner.parse_args([*base, "--paper", "--inverse", "--variant", "inverse"])
    assert (demo.reverse_on_stop, demo.inverse) == (0.5, False)
    assert (paper.reverse_on_stop, paper.inverse, paper.variant) == (None, True, "inverse")


def test_reverse_on_stop_is_refused_with_weekend_flat() -> None:
    with pytest.raises(SystemExit):
        run_live_nasdaq_orb.parse_args(["--symbol", "XAUUSD", "--tp-r", "3", "--weekend-flat",
                                        "--reverse-on-stop", "0.5"])


class FakeBroker:
    def __init__(self, positions: list[Position], pending: list[PendingOrder] | None = None) -> None:
        self.positions, self.pending = positions, list(pending or [])
        self.placed: list[OrderRequest] = []
        self.canceled: list[str] = []

    def get_open_positions(self) -> list[Position]:
        return list(self.positions)

    def get_pending_orders(self, symbol: str) -> list[PendingOrder]:
        return [o for o in self.pending if o.symbol == symbol]

    def get_symbol_constraints(self, symbol: str) -> SymbolConstraints:
        return SymbolConstraints(symbol=symbol, contract_size=1.0, tick_size=0.01, tick_value=0.01,
                                 volume_min=0.01, volume_max=100.0, volume_step=0.01)

    def place_order(self, order: OrderRequest) -> OrderResult:
        self.placed.append(order)
        return OrderResult(success=True, order_id="900", position_id="900", retcode=10008)

    def cancel_order(self, order_id: str) -> bool:
        self.canceled.append(order_id)
        return True


class FakeConnector:
    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars

    def fetch_recent_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
        return self.bars


def _bars(price: float = 100.0) -> list[Bar]:
    return [Bar(timestamp=NOW - timedelta(minutes=15 * (3 - i)), open=price, high=price + 0.2,
                low=price - 0.2, close=price, volume=1.0) for i in range(3)]


def _run_once(runner, broker, tmp_path, symbol: str, reverse_on_stop_r: float | None = 0.5) -> None:
    runner.run_once(
        connector=FakeConnector(_bars()), broker=broker, trade_manager=TradeManager(volume=0.1),
        strategy=SimpleNamespace(evaluate=lambda _s: None,
                                 diagnostics=SimpleNamespace(summary=lambda: {})),
        symbol=symbol, timeframe=Timeframe.M15, timeframe_str="M15", lookback_days=1,
        kill_switch_flag_path=tmp_path / "no_kill_switch.flag",
        traded_setups_path=tmp_path / "ledger.json", reverse_on_stop_r=reverse_on_stop_r)


@pytest.mark.parametrize("runner, symbol", [(run_live_nasdaq_orb, "XAUUSD"), (run_live_xauusd_orb, "GER40")])
def test_an_open_trade_gets_its_reverse_order_during_the_poll(runner, symbol, tmp_path, events) -> None:
    original = Position(id="12884987", symbol=symbol, order_type=OrderType.BUY_MARKET, volume=0.16,
                        open_price=101.0, current_price=100.0, stop_loss=99.0, take_profit=105.0,
                        timestamp=NOW - timedelta(hours=1), comment=f"{runner.STRATEGY_TAG}__9f089d26")
    broker = FakeBroker([original])
    _run_once(runner, broker, tmp_path, symbol)
    [order] = broker.placed
    assert (order.order_type, order.price, order.stop_loss, order.take_profit, order.volume) == (
        OrderType.SELL_STOP, 99.0, 101.0, 98.0, 0.16)
    assert order.comment == reverse_comment(runner.STRATEGY_TAG, "12884987")
    assert "reverse_order_placed" in [name for name, _ in events]


@pytest.mark.parametrize("runner, symbol", [(run_live_nasdaq_orb, "XAUUSD"), (run_live_xauusd_orb, "GER40")])
def test_a_flat_bot_cancels_the_order_its_closed_trade_left_behind(runner, symbol, tmp_path, events) -> None:
    left = PendingOrder(id="501", symbol=symbol, order_type=OrderType.SELL_STOP, volume=0.16, price=99.0,
                        comment=reverse_comment(runner.STRATEGY_TAG, "12884987"))
    broker = FakeBroker([], [left])
    _run_once(runner, broker, tmp_path, symbol)
    assert (broker.canceled, broker.placed) == (["501"], [])


@pytest.mark.parametrize("runner, symbol", [(run_live_nasdaq_orb, "XAUUSD"), (run_live_xauusd_orb, "GER40")])
def test_without_the_flag_a_reverse_order_left_from_when_it_had_one_is_cancelled(
        runner, symbol, tmp_path, events) -> None:
    # 2026-09-22: the flag came off every launcher on 2026-09-21, and FundingPips still held two
    # such orders a day later -- the old rule here ("without the flag, touch nothing") kept them.
    left = PendingOrder(id="501", symbol=symbol, order_type=OrderType.SELL_STOP, volume=0.16, price=99.0,
                        comment=reverse_comment(runner.STRATEGY_TAG, "12884987"))
    broker = FakeBroker([], [left])
    _run_once(runner, broker, tmp_path, symbol, reverse_on_stop_r=None)
    assert (broker.canceled, broker.placed) == (["501"], [])
    assert "reverse_order_canceled" in [name for name, _ in events]


@pytest.mark.parametrize("runner, symbol", [(run_live_nasdaq_orb, "XAUUSD"), (run_live_xauusd_orb, "GER40")])
def test_without_the_flag_an_open_trade_gets_no_reverse_order_and_others_orders_stay(
        runner, symbol, tmp_path, events) -> None:
    original = Position(id="12884987", symbol=symbol, order_type=OrderType.BUY_MARKET, volume=0.16,
                        open_price=101.0, current_price=100.0, stop_loss=99.0, take_profit=105.0,
                        timestamp=NOW - timedelta(hours=1), comment=f"{runner.STRATEGY_TAG}__9f089d26")
    foreign = PendingOrder(id="777", symbol=symbol, order_type=OrderType.BUY_LIMIT, volume=0.1, price=98.0,
                           comment="setup_first_fvg_window_limit")
    broker = FakeBroker([original], [foreign])
    _run_once(runner, broker, tmp_path, symbol, reverse_on_stop_r=None)
    assert (broker.canceled, broker.placed) == ([], [])


class _Capture:
    """Stands in for TradeManager: fills everything and remembers the setup it was given."""

    def __init__(self) -> None:
        self.setups: list[TradeSetup] = []
        self.last_open_result = None
        self._position_sizer = None

    def open_trade(self, setup, broker):
        self.setups.append(setup)
        return SimpleNamespace(status=OrderStatus.FILLED, fill_price=setup.entry_zone[0], order_id="1")


@pytest.mark.parametrize("runner", [run_live_nasdaq_orb, run_live_xauusd_orb])
def test_an_inverse_bot_opens_the_mirror_of_its_signal(runner, tmp_path, events) -> None:
    bars = _bars()
    setup = TradeSetup(
        setup_id=f"{runner.STRATEGY_TAG}_XAUUSD_M15_BUY_20260916", symbol="XAUUSD", timeframe=Timeframe.M15,
        direction=SignalDirection.BUY, entry_zone=(100.0, 100.0), stop_zone=(99.0, 99.0),
        target_zone=(104.0, 104.0), confidence_score=1.0, confluence=[], trigger_reason="breakout",
        invalidations=[], related_structure_break=None, related_order_block=None, related_fvg=None,
        timestamp=bars[-1].timestamp)
    strategy = SimpleNamespace(evaluate=lambda state: setup if state.bar_count() == len(bars) else None,
                               diagnostics=SimpleNamespace(summary=lambda: {}))
    capture = _Capture()
    runner._evaluate_for_new_trade(capture, None, strategy, bars, "XAUUSD", Timeframe.M15, 1,
                                   kill_switch_flag_path=tmp_path / "none.flag",
                                   traded_setups_path=tmp_path / "ledger.json", inverse=True)
    [opened] = capture.setups
    assert opened.direction == SignalDirection.SELL
    assert (opened.stop_zone, opened.target_zone) == ((104.0, 104.0), (99.0, 99.0))
    assert opened.setup_id.endswith("_inv")
