"""run_live_amd.py: the demo-account gates, the session rules, and one full poll cycle on the synthetic day
from test_gold_amd.py (entry due 11:45 New York, session flat at 15:55)."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import numpy as np
import pytest

import run_live_amd as runner
import strategy.gold_amd as gold_amd
from core.models import AccountInfo, OrderType, SymbolConstraints
from execution.models import Position
from execution.paper_broker import PaperBroker
from execution.position_sizer import PositionSizer
from execution.trade_manager import TradeManager
from mt5.connector import MT5Connector
from strategy.gold_amd import GoldAmdConfig, GoldAmdStrategy
from tests.test_gold_amd import ENTRY_MINUTE, LONG_PATH, T0, bars_of

NY = ZoneInfo("America/New_York")
CONFIG = GoldAmdConfig()


def ny(hour: int, minute: int, day: int = 11, month: int = 3, second: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, second, tzinfo=NY)


def position(opened: datetime, comment: str = "setup_amd_20260311_L") -> Position:
    return Position(id="p1", symbol="XAUUSD_", order_type=OrderType.BUY_MARKET, volume=0.1, open_price=100.0,
                    current_price=100.0, stop_loss=99.0, take_profit=102.0, timestamp=opened, comment=comment)


def test_paper_is_a_choice_and_the_demo_path_is_the_default():
    assert runner.parse_args(["--symbol", "XAUUSD", "--paper"]).paper is True
    assert runner.parse_args(["--symbol", "XAUUSD"]).paper is False


def test_the_demo_path_needs_the_env_to_say_demo(monkeypatch):
    monkeypatch.setattr(runner.Settings, "load", classmethod(lambda cls: Mock(MT5_ACCOUNT_TYPE="live")))
    with pytest.raises(runner.DemoAccountRequiredError, match="MT5_ACCOUNT_TYPE"):
        runner._ensure_explicit_demo_configuration()
    monkeypatch.setattr(runner.Settings, "load", classmethod(lambda cls: Mock(MT5_ACCOUNT_TYPE="  Demo ")))
    runner._ensure_explicit_demo_configuration()


@pytest.mark.parametrize(("trade_mode", "ok"), [(0, True), (1, False), (2, False)])
def test_the_demo_path_needs_the_account_to_report_demo(trade_mode, ok):
    info = AccountInfo(balance=1.0, equity=1.0, margin=0.0, free_margin=1.0, trade_mode=trade_mode)
    if ok:
        runner._ensure_demo_trade_mode(info)
    else:
        with pytest.raises(runner.DemoAccountRequiredError, match="LIVE"):
            runner._ensure_demo_trade_mode(info)


def test_a_demo_run_refuses_to_start_on_a_live_env_and_connects_to_nothing(monkeypatch):
    monkeypatch.setattr(runner, "resolve_ticker", lambda s: (Mock(name="cfi", server="CFI11-Demo"), "XAUUSD_"))
    monkeypatch.setattr(runner.Settings, "load", classmethod(lambda cls: Mock(MT5_ACCOUNT_TYPE="live")))
    monkeypatch.setattr(runner, "MT5Connector", Mock(side_effect=AssertionError("must not connect")))
    with pytest.raises(SystemExit):
        runner.main(["--symbol", "XAUUSD"])


def test_paper_and_demo_keep_separate_ledgers(tmp_path):
    paper = runner._traded_setups_path(tmp_path, "xauusd_", True)
    demo = runner._traded_setups_path(tmp_path, "xauusd_", False)
    assert paper != demo and paper.name.endswith("_paper.json") and not demo.name.endswith("_paper.json")


@pytest.mark.parametrize(("when", "expected"), [
    (ny(9, 59), False), (ny(10, 0), True), (ny(14, 59), True), (ny(15, 0), False),
    (ny(11, 0, day=14), False),          # a Saturday
])
def test_entries_are_only_taken_inside_the_new_york_window(when, expected):
    assert runner.in_entry_window(when, CONFIG) is expected


@pytest.mark.parametrize(("opened", "now", "expected"), [
    (ny(11, 45), ny(15, 54), False),
    (ny(11, 45), ny(15, 55), True),                   # session end
    (ny(11, 45), ny(11, 50), False),
    (ny(14, 50), ny(8, 0, day=12), True),             # left over, and before the 10:00 open
    (ny(14, 50), ny(11, 0, day=12), True),            # left over from the day before
])
def test_a_position_is_closed_at_the_session_end_or_when_it_is_left_over(opened, now, expected):
    assert runner.flat_due(position(opened), now, CONFIG) is expected


def test_only_this_bots_positions_are_its_own():
    mine, foreign = runner._partition_positions(
        [position(ny(11, 45)), position(ny(11, 45), comment="setup_nasdaq_orb_m1_XAUUSD_M1_BUY")], "XAUUSD_")
    assert [p.comment for p in mine] == ["setup_amd_20260311_L"]
    assert len(foreign) == 1


@pytest.fixture
def rig(tmp_path: Path, monkeypatch):
    """A PaperBroker over the synthetic day, whose clock the test moves."""
    monkeypatch.setattr(gold_amd, "bias_series", lambda f4: (f4.tc, np.ones(len(f4.tc), dtype=np.int8)))
    monkeypatch.setattr(runner, "_clock_trustworthy", lambda symbol: True)
    day_bars = bars_of(LONG_PATH, lambda f: None)
    state = {"now": ny(11, 45, second=20)}

    def closed_bars(symbol, timeframe, count):
        upto = int(state["now"].timestamp())
        return [b for b in day_bars if int(b.timestamp.timestamp()) + 60 <= upto][-count:]

    connector = Mock(spec=MT5Connector)
    connector.fetch_recent_bars.side_effect = closed_bars
    connector.fetch_symbol_info.return_value = SymbolConstraints(
        symbol="XAUUSD_", contract_size=100.0, tick_size=0.01, tick_value=1.0, volume_min=0.01, volume_max=5.0,
        volume_step=0.01)
    connector.fetch_account_info.return_value = Mock(leverage=100)
    broker = PaperBroker(connector=connector, timeframe="M1", state_file=tmp_path / "paper.json")
    trade_manager = TradeManager(position_sizer=PositionSizer(risk_per_trade_pct=0.005))
    strategy = GoldAmdStrategy(GoldAmdConfig(min_fvg=0.5))

    def poll():
        runner.run_once(connector, broker, trade_manager, strategy, "XAUUSD_", 21, tmp_path / "halt.flag",
                        tmp_path / "traded.json", now=state["now"])

    return SimpleRig(state, connector, broker, poll, tmp_path / "traded.json")


class SimpleRig:
    def __init__(self, state, connector, broker, poll, ledger):
        self.state, self.connector, self.broker, self.poll, self.ledger = state, connector, broker, poll, ledger


def test_a_full_day_opens_once_holds_and_flattens_at_the_session_end(rig):
    rig.poll()                                                    # 11:45:20 -- the entry is due
    [opened] = rig.broker.get_open_positions()
    assert opened.comment == "setup_amd_20260311_L" and opened.symbol == "XAUUSD_"
    assert opened.stop_loss < opened.open_price < opened.take_profit
    assert "setup_amd_20260311_L" in rig.ledger.read_text()

    rig.state["now"] = ny(11, 47, second=20)                      # the next poll: held, not opened again
    rig.poll()
    assert len(rig.broker.get_open_positions()) == 1

    rig.state["now"] = ny(15, 56)                                 # session over: closed at market
    rig.poll()
    assert rig.broker.get_open_positions() == []

    rig.state["now"] = ny(11, 46, second=20)                      # signal still fresh (80 s): the ledger stops it
    rig.poll()
    assert rig.broker.get_open_positions() == []


def test_outside_the_window_no_bars_are_fetched(rig):
    rig.state["now"] = ny(8, 0)
    rig.poll()
    rig.connector.fetch_recent_bars.assert_not_called()
    assert rig.broker.get_open_positions() == []


def test_a_kill_switch_stops_a_signal_from_opening(rig, tmp_path):
    (tmp_path / "halt.flag").write_text("halt")
    rig.poll()
    assert rig.broker.get_open_positions() == []
