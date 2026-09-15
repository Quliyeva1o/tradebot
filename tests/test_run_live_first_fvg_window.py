"""Tests for run_live_first_fvg_window.py's run_once -- what one poll does with a day's setup.

The day's M15 setup (NY time, 2026-09-14): candle 1 at 09:45 (high 100, low 90), the 10:00
candle, candle 3 at 10:15 (low 105) -> buy limit 105, stop 90, target 150, working from
10:30 NY (14:30 UTC) until NY midnight (2026-09-15 04:00 UTC). Sizing: 0.5% of 50,000 = 250
at risk over 15 ticks worth 1.0 each = 16.67 lots, floored to the 0.01 step = 16.66.
"""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import risk.kill_switch as kill_switch_module
import run_live_first_fvg_window as runner
from core.models import AccountInfo, Bar, OrderType, SymbolConstraints
from execution.models import OrderRequest, OrderResult, Position
from execution.position_sizer import PositionSizer
from strategy.first_fvg_window import FirstFvgWindowConfig

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 14, 14, 32, tzinfo=UTC)
SETUP_ID = "setup_fvg_window_NDX100_20260914_BUY"


@pytest.fixture(autouse=True)
def _isolated_kill_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kill_switch_module, "KILL_SWITCH_FLAG", tmp_path / "kill_switch.flag")


def _ny(hh: int, mm: int, o: float, h: float, l: float, c: float, day: int = 14) -> Bar:
    return Bar(timestamp=datetime(2026, 9, day, hh, mm, tzinfo=NY).astimezone(UTC),
               open=o, high=h, low=l, close=c, volume=1.0)


class _Connector:
    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars

    def fetch_recent_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
        assert (symbol, timeframe) == ("NDX100", "M15")
        return self.bars[-count:]


class _Broker:
    def __init__(self, positions: list[Position] | None = None) -> None:
        self.positions = positions or []
        self.requests: list[OrderRequest] = []

    def get_open_positions(self) -> list[Position]:
        return list(self.positions)

    def get_account_info(self) -> AccountInfo:
        return AccountInfo(balance=50_000.0, equity=50_000.0, margin=0.0, free_margin=50_000.0)

    def get_symbol_constraints(self, symbol: str) -> SymbolConstraints:
        return SymbolConstraints(symbol=symbol, contract_size=1.0, tick_size=1.0, tick_value=1.0,
                                 volume_min=0.01, volume_max=100.0, volume_step=0.01)

    def place_order(self, request: OrderRequest) -> OrderResult:
        self.requests.append(request)
        return OrderResult(success=True, order_id="42")


SETUP_BARS = [_ny(9, 30, 90, 95, 85, 92), _ny(9, 45, 92, 100, 90, 98),
              _ny(10, 0, 98, 121, 97, 120), _ny(10, 15, 119, 125, 105, 124)]


def _position(comment: str) -> Position:
    return Position(id="p1", symbol="NDX100", order_type=OrderType.BUY_MARKET, volume=1.0,
                    open_price=100.0, current_price=100.0, stop_loss=90.0, take_profit=150.0,
                    comment=comment)


def _poll(broker: _Broker, tmp_path: Path, bars: list[Bar] = SETUP_BARS, now: datetime = NOW,
          kill_switch: Path | None = None) -> str:
    return runner.run_once(
        connector=_Connector(bars), broker=broker, sizer=PositionSizer(risk_per_trade_pct=0.005),
        symbol="NDX100", cfg=FirstFvgWindowConfig(), now=now,
        traded_setups_path=tmp_path / "traded.json", kill_switch_flag_path=kill_switch,
    )


def test_places_the_days_limit_order_with_its_window(tmp_path: Path) -> None:
    broker = _Broker()

    outcome = _poll(broker, tmp_path)

    assert outcome == "order_placed"
    [req] = broker.requests
    assert (req.order_type, req.price, req.stop_loss, req.take_profit) == (OrderType.BUY_LIMIT, 105.0, 90.0, 150.0)
    assert req.volume == pytest.approx(16.66)
    assert req.comment == SETUP_ID
    assert req.valid_from == datetime(2026, 9, 14, 14, 30, tzinfo=UTC)
    assert req.expires_at == datetime(2026, 9, 15, 4, 0, tzinfo=UTC)


def test_a_setup_is_ordered_only_once(tmp_path: Path) -> None:
    broker = _Broker()
    _poll(broker, tmp_path)

    outcome = _poll(broker, tmp_path, now=datetime(2026, 9, 14, 14, 34, tzinfo=UTC))

    assert outcome == "already_traded"
    assert len(broker.requests) == 1


def test_setup_confirmed_while_our_trade_is_open_is_given_up(tmp_path: Path) -> None:
    # The backtest skips a setup whose candle 3 closes while the previous trade is open;
    # the bot must not place it later once that trade has closed.
    busy = _Broker(positions=[_position("setup_fvg_window_NDX100_20260911_BUY")])
    assert _poll(busy, tmp_path) == "held"

    free = _Broker()
    assert _poll(free, tmp_path, now=datetime(2026, 9, 14, 15, 0, tzinfo=UTC)) == "already_traded"
    assert free.requests == []


def test_another_bots_position_on_the_symbol_blocks_the_order(tmp_path: Path) -> None:
    broker = _Broker(positions=[_position("setup_nasdaq_orb_m1_NDX100_x")])

    assert _poll(broker, tmp_path) == "foreign_position"
    assert broker.requests == []


def test_day_without_a_setup_places_nothing(tmp_path: Path) -> None:
    broker = _Broker()
    no_gap = [_ny(9, 45, 100, 105, 95, 100), _ny(10, 0, 100, 105, 95, 100), _ny(10, 15, 100, 105, 95, 100)]

    assert _poll(broker, tmp_path, bars=no_gap) == "no_setup"
    assert broker.requests == []


def test_yesterdays_setup_is_not_ordered_today(tmp_path: Path) -> None:
    broker = _Broker()

    outcome = _poll(broker, tmp_path, now=datetime(2026, 9, 15, 14, 32, tzinfo=UTC))

    assert outcome == "no_setup"
    assert broker.requests == []


def test_active_kill_switch_places_nothing(tmp_path: Path) -> None:
    flag = tmp_path / "kill.flag"
    flag.write_text("halted for test")
    broker = _Broker()

    assert _poll(broker, tmp_path, kill_switch=flag) == "kill_switch"
    assert broker.requests == []


def test_refuses_to_start_without_paper_mode() -> None:
    with pytest.raises(SystemExit):
        runner.main(["--symbol", "NDX100"])
