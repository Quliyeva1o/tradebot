"""Tests for run_live_first_fvg_window.py's run_once -- what one poll does with a day's setup.

The day's M15 setup (NY time, 2026-09-14): candle 1 at 09:45 (high 100, low 90), the 10:00
candle, candle 3 at 10:15 (low 105) -> buy limit 105, stop 90, target 150, working from
10:30 NY (14:30 UTC) until NY midnight (2026-09-15 04:00 UTC). Sizing: 0.5% of 50,000 = 250
at risk over 15 ticks worth 1.0 each = 16.67 lots, floored to the 0.01 step = 16.66.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import risk.kill_switch as kill_switch_module
import run_live_first_fvg_window as runner
from core.models import AccountInfo, Bar, OrderType, SymbolConstraints
from execution.models import OrderRequest, OrderResult, PendingOrder, Position
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


class _LiveBroker(_Broker):
    """A real-broker double: resting orders to sweep, and a tick grid finer than the plan's prices."""

    def __init__(self, pending: list[PendingOrder] | None = None, tick_size: float = 1.0) -> None:
        super().__init__()
        self.pending = pending or []
        self.cancelled: list[str] = []
        self.tick_size = tick_size

    def get_symbol_constraints(self, symbol: str) -> SymbolConstraints:
        return SymbolConstraints(symbol=symbol, contract_size=1.0, tick_size=self.tick_size, tick_value=1.0,
                                 volume_min=0.01, volume_max=100.0, volume_step=0.01)

    def get_pending_orders(self, symbol: str) -> list[PendingOrder]:
        return [o for o in self.pending if o.symbol == symbol]

    def cancel_order(self, order_id: str) -> bool:
        self.cancelled.append(order_id)
        return True


def _live_poll(broker: _LiveBroker, tmp_path: Path, quote: runner.Quote, bars: list[Bar] = SETUP_BARS,
               now: datetime = NOW) -> str:
    return runner.run_once(
        connector=_Connector(bars), broker=broker, sizer=PositionSizer(risk_per_trade_pct=0.005),
        symbol="NDX100", cfg=FirstFvgWindowConfig(), now=now,
        traded_setups_path=tmp_path / "traded.json", quote=lambda symbol: quote,
    )


def _pending(order_id: str, comment: str, expires_at: datetime | None) -> PendingOrder:
    return PendingOrder(id=order_id, symbol="NDX100", order_type=OrderType.BUY_LIMIT, volume=1.0, price=105.0,
                        stop_loss=90.0, take_profit=150.0, comment=comment, expires_at=expires_at)


# The 2026-09-21 CFI setup: 30144.98 + 3 * 97.37 is 30437.089999999997 in floating point.
OFF_GRID_BARS = [_ny(9, 45, 30100, 30120, 30047.61, 30110), _ny(10, 0, 30110, 30200, 30105, 30190),
                 _ny(10, 15, 30190, 30220, 30144.98, 30210)]


class TestLiveEntry:
    """The setup is a buy limit at 105 (stop 90, target 150); what goes in depends on the live quote."""

    def test_rests_the_limit_while_price_is_above_it(self, tmp_path: Path) -> None:
        broker = _LiveBroker()

        assert _live_poll(broker, tmp_path, runner.Quote(bid=119.0, ask=120.0, min_gap=0.1)) == "order_placed"
        [req] = broker.requests
        assert (req.order_type, req.price, req.stop_loss, req.take_profit) == (OrderType.BUY_LIMIT, 105.0, 90.0, 150.0)
        assert req.expires_at == datetime(2026, 9, 15, 4, 0, tzinfo=UTC)

    def test_enters_at_market_once_price_has_come_back_through_the_limit(self, tmp_path: Path) -> None:
        # MT5 refuses a buy limit above the ask; the backtest fills this bar at its better open.
        broker = _LiveBroker()

        assert _live_poll(broker, tmp_path, runner.Quote(bid=103.0, ask=104.0, min_gap=0.1)) == "entered_at_market"
        [req] = broker.requests
        assert (req.order_type, req.price, req.stop_loss, req.take_profit) == (OrderType.BUY_MARKET, None, 90.0, 150.0)
        assert (req.valid_from, req.expires_at) == (None, None)

    def test_a_limit_inside_the_brokers_minimum_distance_also_goes_at_market(self, tmp_path: Path) -> None:
        broker = _LiveBroker()

        assert _live_poll(broker, tmp_path, runner.Quote(bid=104.0, ask=105.05, min_gap=0.1)) == "entered_at_market"

    def test_price_already_through_the_stop_is_not_entered_and_not_retried(self, tmp_path: Path) -> None:
        broker = _LiveBroker()

        assert _live_poll(broker, tmp_path, runner.Quote(bid=89.5, ask=90.5, min_gap=0.1)) == "price_beyond_stop"
        assert broker.requests == []
        assert _live_poll(broker, tmp_path, runner.Quote(bid=119.0, ask=120.0, min_gap=0.1),
                          now=datetime(2026, 9, 14, 14, 34, tzinfo=UTC)) == "already_traded"

    def test_prices_are_put_on_the_brokers_tick_grid(self, tmp_path: Path) -> None:
        broker = _LiveBroker(tick_size=0.01)

        _live_poll(broker, tmp_path, runner.Quote(bid=30209.0, ask=30210.0, min_gap=0.1), bars=OFF_GRID_BARS)

        [req] = broker.requests
        assert (req.price, req.stop_loss, req.take_profit) == (30144.98, 30047.61, 30437.09)

    def test_paper_keeps_sending_the_plan_unrounded(self, tmp_path: Path) -> None:
        # Paper's limits are matched to their backtest twins by price; rounding is a real broker's need.
        broker = _Broker()

        _poll(broker, tmp_path, bars=OFF_GRID_BARS)

        assert broker.requests[0].take_profit == 30144.98 + 3 * (30144.98 - 30047.61)


class TestExpirySweep:
    """Each live poll cancels an order of ours past its expiry -- the second line behind MT5's own."""

    def test_cancels_only_our_orders_past_expiry_or_without_one(self, tmp_path: Path) -> None:
        broker = _LiveBroker(pending=[
            _pending("1", "setup_fvg_window_NDX_ab12cd34", datetime(2026, 9, 14, 4, 0, tzinfo=UTC)),
            _pending("2", "setup_fvg_window_NDX_ef56ab78", datetime(2026, 9, 15, 4, 0, tzinfo=UTC)),
            _pending("3", "setup_fvg_window_NDX_99887766", None),
            _pending("4", "setup_nasdaq_orb_m1_sar884987", datetime(2026, 9, 14, 4, 0, tzinfo=UTC)),
        ])

        _live_poll(broker, tmp_path, runner.Quote(bid=119.0, ask=120.0, min_gap=0.1))

        assert broker.cancelled == ["1", "3"]

    def test_paper_leaves_expiry_to_the_paper_broker(self, tmp_path: Path) -> None:
        broker = _LiveBroker(pending=[
            _pending("1", "setup_fvg_window_NDX_ab12cd34", datetime(2026, 9, 14, 4, 0, tzinfo=UTC)),
        ])

        _poll(broker, tmp_path)

        assert broker.cancelled == []

    def test_halted_cancels_every_order_of_ours(self) -> None:
        broker = _LiveBroker(pending=[
            _pending("2", "setup_fvg_window_NDX_ef56ab78", datetime(2026, 9, 15, 4, 0, tzinfo=UTC)),
            _pending("4", "setup_nasdaq_orb_m1_sar884987", datetime(2026, 9, 15, 4, 0, tzinfo=UTC)),
        ])

        assert runner.cancel_our_orders(broker, "NDX100", NOW, all_of_them=True) == 1
        assert broker.cancelled == ["2"]


def test_refuses_real_orders_unless_env_says_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "resolve_ticker",
                        lambda symbol: (SimpleNamespace(name="cfi", server="CFI11-Demo"), "US100_Spot"))
    monkeypatch.setattr(runner.Settings, "load", staticmethod(lambda: SimpleNamespace(MT5_ACCOUNT_TYPE="live")))

    def _no_terminal() -> None:
        raise AssertionError("must refuse before touching MT5")

    monkeypatch.setattr(runner, "MT5Connector", _no_terminal)

    with pytest.raises(SystemExit):
        runner.main(["--symbol", "NDX100"])
