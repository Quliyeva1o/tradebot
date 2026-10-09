"""ict_lab/execute.py: the ICT A+ DEMO order bot's decisions, against a fake broker (no MT5 involved)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.models import AccountInfo, OrderType, SymbolConstraints
from execution.models import OrderResult, PendingOrder, Position
from ict_lab import execute as E

NOW = datetime(2026, 10, 7, 14, 30, tzinfo=UTC)          # 10:30 New York
US100 = SymbolConstraints("US100_Spot", 20.0, 0.01, 0.2, 0.01, 20.0, 0.01)


class FakeBroker:
    def __init__(self, balance: float = 50_000.0) -> None:
        self.balance = balance
        self.placed: list = []
        self.cancelled: list = []
        self.closed: list = []
        self.pending: list[PendingOrder] = []
        self.positions: list[Position] = []
        self._n = 100

    def get_open_positions(self):
        return list(self.positions)

    def get_pending_orders(self, symbol):
        return [o for o in self.pending if o.symbol == symbol]

    def get_account_info(self):
        return AccountInfo(balance=self.balance, equity=self.balance, margin=0.0, free_margin=self.balance, login=1)

    def get_symbol_constraints(self, symbol):
        return US100

    def place_order(self, req):
        self._n += 1
        self.placed.append(req)
        self.pending.append(PendingOrder(id=str(self._n), symbol=req.symbol, order_type=req.order_type, volume=req.volume,
                                         price=req.price, stop_loss=req.stop_loss, take_profit=req.take_profit,
                                         comment=req.comment, expires_at=req.expires_at))
        return OrderResult(success=True, order_id=str(self._n), position_id=str(self._n), price=req.price, volume=req.volume)

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        self.pending = [o for o in self.pending if o.id != order_id]
        return True

    def close_position(self, position_id):
        self.closed.append(position_id)
        self.positions = [p for p in self.positions if p.id != position_id]
        return OrderResult(success=True, order_id="c", position_id=position_id)


@dataclass
class FakeMarket:
    bid: float = 30_400.0
    ask: float = 30_400.8
    result: float | None = None
    override: dict = field(default_factory=dict)

    def quote(self, ticker):
        return (self.bid, self.ask)

    def digits(self, ticker):
        return 2

    def stops_level_price(self, ticker):
        return 0.10

    def position_result(self, position_id):
        return self.result


def args(**kw):
    ns = E.build_parser().parse_args([])
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def cand(**kw) -> E.Candidate:
    base = dict(sid="NDX100|ote|20733|ny_am|1|swing|1791383100", name="NDX100", ticker="US100_Spot", strat="ote", win="ny_am",
                d=1, grade="A+", mode="limit", sig_ts=int((NOW - timedelta(minutes=3)).timestamp()), state="pending", why="",
                limit=30_380.0, sl=30_350.0, expire_utc=NOW + timedelta(hours=1), hold_utc=NOW + timedelta(hours=5),
                floor=0.8)
    base.update(kw)
    return E.Candidate(**base)


def make(tmp_path: Path, broker=None, market=None, **kw):
    broker = broker or FakeBroker()
    ex = E.Executor(broker, market or FakeMarket(), args(**kw), tmp_path / "state.json", report=lambda k, **f: events.append((k, f)))
    return ex, broker


events: list = []


@pytest.fixture(autouse=True)
def _clear_events():
    events.clear()


def kinds():
    return [k for k, _ in events]


# ---------------------------------------------------------------- tags / time

def test_tag_is_short_stable_and_prefixed():
    t = E.tag_of("NDX100|ote|20733|ny_am|1|swing|1791383100")
    assert t.startswith("ict") and len(t) <= 16
    assert t == E.tag_of("NDX100|ote|20733|ny_am|1|swing|1791383100")
    assert t != E.tag_of("NDX100|ote|20733|ny_am|-1|swing|1791383100")


def test_new_york_minutes_become_the_right_utc_instant_in_summer_and_winter():
    summer = (datetime(2026, 10, 9).date() - datetime(1970, 1, 1).date()).days * 1440 + 16 * 60      # 16:00 NY, EDT
    winter = (datetime(2026, 12, 9).date() - datetime(1970, 1, 1).date()).days * 1440 + 16 * 60      # 16:00 NY, EST
    assert E.ny_minutes_to_utc(summer) == datetime(2026, 10, 9, 20, 0, tzinfo=UTC)
    assert E.ny_minutes_to_utc(winter) == datetime(2026, 12, 9, 21, 0, tzinfo=UTC)


# ---------------------------------------------------------------- placing

def test_places_a_resting_limit_with_stop_target_and_expiry(tmp_path):
    ex, broker = make(tmp_path)
    ex.reconcile([cand()], NOW)
    assert len(broker.placed) == 1
    o = broker.placed[0]
    assert o.order_type is OrderType.BUY_LIMIT and o.symbol == "US100_Spot"
    assert o.price == 30_380.0 and o.stop_loss == 30_350.0
    assert o.take_profit == pytest.approx(30_380.0 + 2 * 30.0)             # 2R from the limit
    assert o.expires_at == NOW + timedelta(hours=1)
    assert o.comment == E.tag_of(cand().sid)
    # 0.25% of 50,000 = 125 risked on a 30-point stop: 125 / (30/0.01*0.2) = 0.2083 -> 0.20 lots
    assert o.volume == pytest.approx(0.20)
    assert ex.state["setups"][cand().sid]["status"] == "pending"
    assert "placed" in kinds()


def test_short_uses_a_sell_limit_and_the_target_below(tmp_path):
    ex, broker = make(tmp_path)
    ex.reconcile([cand(d=-1, limit=30_420.0, sl=30_450.0)], NOW)
    o = broker.placed[0]
    assert o.order_type is OrderType.SELL_LIMIT
    assert o.take_profit == pytest.approx(30_420.0 - 60.0)


def test_never_places_the_same_setup_twice(tmp_path):
    ex, broker = make(tmp_path)
    ex.reconcile([cand()], NOW)
    ex.reconcile([cand()], NOW + timedelta(minutes=1))
    assert len(broker.placed) == 1
    # ... not even after the order has gone (filled / expired): the record remembers it
    broker.pending.clear()
    ex.reconcile([cand()], NOW + timedelta(minutes=2))
    assert len(broker.placed) == 1


def test_a_setup_first_seen_late_is_not_chased(tmp_path):
    ex, broker = make(tmp_path)
    ex.reconcile([cand(sig_ts=int((NOW - timedelta(minutes=40)).timestamp()))], NOW)
    assert broker.placed == [] and "skip" in kinds()
    assert ex.state["setups"][cand().sid]["reason"] == "stale_not_chased"


def test_market_mode_setups_are_skipped(tmp_path):
    ex, broker = make(tmp_path)
    ex.reconcile([cand(mode="market", limit=None)], NOW)
    assert broker.placed == [] and events[0][1]["reason"] == "market_mode_not_traded"


def test_a_stop_under_five_spreads_is_refused(tmp_path):
    ex, broker = make(tmp_path)
    ex.reconcile([cand(sl=30_377.0)], NOW)                                   # 3 points < 5 x 0.8
    assert broker.placed == [] and events[0][1]["reason"] == "stop_under_5_spreads"


def test_a_wide_spread_waits_without_burning_the_setup(tmp_path):
    ex, broker = make(tmp_path, market=FakeMarket(bid=30_400.0, ask=30_405.0))
    ex.reconcile([cand()], NOW)
    assert broker.placed == [] and events[0][1]["reason"] == "spread_too_wide"
    assert cand().sid not in ex.state["setups"]                              # tried again next cycle
    ex.market = FakeMarket()
    ex.reconcile([cand()], NOW + timedelta(minutes=1))
    assert len(broker.placed) == 1


def test_a_limit_that_the_market_already_crossed_waits(tmp_path):
    ex, broker = make(tmp_path, market=FakeMarket(bid=30_370.0, ask=30_370.8))   # ask below the buy limit
    ex.reconcile([cand()], NOW)
    assert broker.placed == [] and events[0][1]["reason"] == "limit_too_close_to_market"


def test_the_minimum_lot_may_not_overshoot_the_risk(tmp_path):
    tiny = FakeBroker(balance=300.0)                                          # 0.75 risk vs 0.01 lot = 6.0 on this stop
    ex, _ = make(tmp_path, broker=tiny)
    ex.reconcile([cand()], NOW)
    assert tiny.placed == [] and events[-1][1]["reason"] == "min_lot_overshoots_risk"


def test_the_total_risk_cap_stops_a_pile_up(tmp_path):
    ex, broker = make(tmp_path, max_total_risk_pct=0.004)                    # room for one 0.25% trade only
    first = cand()
    second = cand(sid="NDX100|model_2022|20733|ny_am|1|swing|1791383100", strat="model_2022")
    ex.reconcile([first, second], NOW)
    assert len(broker.placed) == 1
    assert any(k == "skip" and f["reason"] == "risk_cap" for k, f in events)


# ---------------------------------------------------------------- following / cancelling

def test_a_dead_setup_gets_its_order_cancelled(tmp_path):
    ex, broker = make(tmp_path)
    ex.reconcile([cand()], NOW)
    ex.reconcile([cand(state="dead", why="invalidated", limit=None)], NOW + timedelta(minutes=2))
    assert broker.cancelled and broker.pending == []
    assert ex.state["setups"][cand().sid]["status"] == "cancelled"


def test_a_setup_the_paper_book_calls_filled_does_not_cancel_the_real_order(tmp_path):
    ex, broker = make(tmp_path)
    ex.reconcile([cand()], NOW)
    ex.reconcile([cand(state="open", limit=None)], NOW + timedelta(minutes=2))
    assert broker.cancelled == [] and len(broker.pending) == 1               # the broker's fill is the truth


def test_a_moved_fib_limit_replaces_the_order(tmp_path):
    ex, broker = make(tmp_path)
    ex.reconcile([cand(mode="fib")], NOW)
    ex.reconcile([cand(mode="fib", limit=30_372.5)], NOW + timedelta(minutes=1))
    assert len(broker.cancelled) == 1 and len(broker.placed) == 2
    assert broker.placed[1].price == 30_372.5
    assert broker.placed[1].comment == broker.placed[0].comment               # same tag, new order
    assert ex.state["setups"][cand().sid]["mods"] == 1


def test_a_limit_that_moved_less_than_a_tick_is_left_alone(tmp_path):
    ex, broker = make(tmp_path)
    ex.reconcile([cand(mode="fib")], NOW)
    ex.reconcile([cand(mode="fib", limit=30_380.004)], NOW + timedelta(minutes=1))
    assert broker.cancelled == [] and len(broker.placed) == 1


def test_an_order_whose_setup_left_the_book_is_cancelled_after_two_cycles(tmp_path):
    ex, broker = make(tmp_path)
    ex.reconcile([cand()], NOW)
    ex.reconcile([], NOW + timedelta(minutes=1))
    assert broker.cancelled == []                                             # one missing cycle is tolerated
    ex.reconcile([], NOW + timedelta(minutes=2))
    assert len(broker.cancelled) == 1 and ex.state["setups"][cand().sid]["reason"] == "setup_gone"


def test_an_order_of_ours_with_no_record_is_cancelled(tmp_path):
    ex, broker = make(tmp_path)
    broker.pending.append(PendingOrder(id="9", symbol="US100_Spot", order_type=OrderType.BUY_LIMIT, volume=0.1, price=1.0,
                                       comment="ictdeadbeef00"))
    ex.state["setups"]["x"] = {"tag": "ictother", "ticker": "US100_Spot", "status": "pending", "expire_utc": NOW.isoformat()}
    ex.reconcile([], NOW)
    assert broker.cancelled == ["9"]


def test_other_bots_orders_are_never_touched(tmp_path):
    ex, broker = make(tmp_path)
    broker.pending.append(PendingOrder(id="7", symbol="US100_Spot", order_type=OrderType.BUY_LIMIT, volume=0.1, price=1.0,
                                       comment="orb_something"))
    broker.positions.append(Position(id="8", symbol="US100_Spot", order_type=OrderType.BUY_MARKET, volume=0.1, open_price=1.0,
                                     current_price=1.0, comment="orb_something", timestamp=NOW - timedelta(days=3)))
    ex.state["setups"]["x"] = {"tag": "ictother", "ticker": "US100_Spot", "status": "pending", "expire_utc": NOW.isoformat()}
    ex.reconcile([], NOW)
    ex.manage_positions(NOW)
    assert broker.cancelled == [] and broker.closed == []


# ---------------------------------------------------------------- positions

def _filled(ex, broker, price=30_380.0):
    ex.reconcile([cand()], NOW)
    o = broker.pending.pop()
    broker.positions.append(Position(id="500", symbol="US100_Spot", order_type=OrderType.BUY_LIMIT, volume=o.volume, open_price=price,
                                     current_price=price, stop_loss=30_350.0, take_profit=30_440.0, comment=o.comment,
                                     timestamp=NOW))


def test_a_fill_is_noticed_and_its_slippage_logged(tmp_path):
    ex, broker = make(tmp_path)
    _filled(ex, broker, price=30_379.5)
    ex.manage_positions(NOW + timedelta(minutes=5))
    assert ex.state["setups"][cand().sid]["status"] == "filled"
    filled = [f for k, f in events if k == "filled"][0]
    assert filled["slippage"] == pytest.approx(-0.5)


def test_an_open_position_is_closed_at_the_flat_time(tmp_path):
    ex, broker = make(tmp_path)
    _filled(ex, broker)
    ex.manage_positions(NOW + timedelta(minutes=5))
    assert broker.closed == []                                                # still before 16:00 New York
    ex.manage_positions(NOW + timedelta(hours=5, minutes=1))
    assert broker.closed == ["500"] and ex.state["setups"][cand().sid]["status"] == "closed"


def test_an_exit_the_broker_made_is_recorded_with_its_result(tmp_path):
    market = FakeMarket(result=-125.0)
    ex, broker = make(tmp_path, market=market)
    _filled(ex, broker)
    ex.manage_positions(NOW + timedelta(minutes=5))
    broker.positions.clear()                                                  # the stop took it
    ex.manage_positions(NOW + timedelta(minutes=10))
    rec = ex.state["setups"][cand().sid]
    assert rec["status"] == "closed" and rec["exit"] == "broker"
    closed = [f for k, f in events if k == "closed"][0]
    assert closed["result_money"] == -125.0 and closed["result_r"] == pytest.approx(-1.0, abs=0.05)


def test_a_vanished_order_is_marked_expired_or_vanished(tmp_path):
    ex, broker = make(tmp_path)
    ex.reconcile([cand()], NOW)
    broker.pending.clear()
    ex.manage_positions(NOW + timedelta(hours=2))                             # past the expiry
    assert ex.state["setups"][cand().sid]["status"] == "expired"


def test_a_position_of_ours_with_no_record_is_flattened_at_the_end_of_its_day(tmp_path):
    ex, broker = make(tmp_path)
    broker.positions.append(Position(id="77", symbol="US100_Spot", order_type=OrderType.BUY_LIMIT, volume=0.1, open_price=1.0,
                                     current_price=1.0, comment="ictlostrecord", timestamp=NOW))
    ex.manage_positions(NOW + timedelta(hours=1))                             # 11:30 NY: keep
    assert broker.closed == []
    ex.manage_positions(NOW + timedelta(hours=5, minutes=30))                 # 16:00 NY: flatten
    assert broker.closed == ["77"]


# ---------------------------------------------------------------- dry run / rails

def test_the_dry_broker_reports_and_sends_nothing(tmp_path):
    seen = []
    inner = FakeBroker()
    dry = E.DryBroker(inner, lambda k, **f: seen.append(k))
    ex = E.Executor(dry, FakeMarket(), args(), tmp_path / "s.json", report=lambda k, **f: events.append((k, f)))
    ex.reconcile([cand()], NOW)
    assert inner.placed == [] and "dry_place" in seen
    ex.manage_positions(NOW + timedelta(minutes=1))                           # no 'vanished' noise: a dry broker holds no orders
    assert "vanished" not in kinds()


def test_guard_demo_requires_both_rails():
    demo = SimpleNamespace(trade_mode=E.mt5.ACCOUNT_TRADE_MODE_DEMO)
    real = SimpleNamespace(trade_mode=E.mt5.ACCOUNT_TRADE_MODE_REAL)
    with pytest.raises(E.DemoRequiredError):
        E.guard_demo(demo, "")                                               # .env does not say demo
    with pytest.raises(E.NotDemoAccountError):
        E.guard_demo(real, "demo")                                            # the account is not a demo one
