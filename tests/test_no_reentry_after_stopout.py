"""A live bot must never open the same setup twice.

The runners rebuild today's setup from scratch on every poll and act on it while
it is inside the signal grace window (see test_signal_grace_window.py). Nothing
remembered that the setup had already been traded. On Demo the stop lives at the
broker and removes the position the instant it is hit, so a stop inside the
window leaves the next poll with no position and an apparently fresh setup.

Replayed on real 2026-09-11 XAUUSD bars (15m OR / 4R): the stop was hit on the
14:02 UTC bar, and the 14:04 poll saw the setup 4 bars old -- inside the 4-bar
window -- so it would have opened the same failed breakout a second time. Paper
never showed it: paper closes on the next poll, by which time the setup had
expired, which is why the VPS log has eight signal_expired lines that day.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from core.models import Bar, Timeframe
from execution.order import OrderStatus
from execution.traded_setups import MAX_REMEMBERED, already_traded, record_traded
from market_structure.structure_models import MarketState
from strategy.nasdaq_orb_m1_breakout import NasdaqOrbM1BreakoutConfig, NasdaqOrbM1BreakoutStrategy

import run_live_nasdaq_orb as nasdaq_runner
import run_live_xauusd_orb as sweep_runner

NY = ZoneInfo("America/New_York")
RUNNERS = [nasdaq_runner, sweep_runner]
BREAKOUT_BAR = 22  # index of the breakout bar in _orb_day()


def _bar(ts: datetime, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(timestamp=ts, open=o, high=h, low=low, close=c, volume=100.0, spread=0.0)


def _orb_day(tail_bars: int) -> list[Bar]:
    """A 15m range of 100.0-101.0 from 09:30, a breakout close at 09:52, then a quiet tail."""
    base = datetime(2026, 9, 11, 9, 30, tzinfo=NY)
    bars = [_bar(base + timedelta(minutes=i), 100.5, 101.0, 100.0, 100.5) for i in range(15)]
    bars += [_bar(base + timedelta(minutes=i), 100.5, 100.9, 100.2, 100.6) for i in range(15, BREAKOUT_BAR)]
    bars.append(_bar(base + timedelta(minutes=BREAKOUT_BAR), 100.9, 102.0, 100.9, 101.8))
    bars += [_bar(base + timedelta(minutes=BREAKOUT_BAR + 1 + i), 101.5, 101.6, 101.2, 101.4)
             for i in range(tail_bars)]
    return bars


def _breakout_strategy() -> NasdaqOrbM1BreakoutStrategy:
    return NasdaqOrbM1BreakoutStrategy(
        config=NasdaqOrbM1BreakoutConfig(or_minutes=15, tp_r=4.0, direction="long")
    )


def _breakout_setup():
    strategy, state, setup = _breakout_strategy(), MarketState(symbol="XAUUSD", timeframe=Timeframe.M1), None
    for b in _orb_day(tail_bars=0):
        state.append_bar(b)
        setup = strategy.evaluate(state) or setup
    assert setup is not None
    return setup


class _ReplayingStrategy:
    """Stands in for the sweep strategy: yields one fixed setup on the breakout bar of a replay."""

    def __init__(self, setup) -> None:
        self._setup, self._calls = setup, 0
        self.diagnostics = SimpleNamespace(summary=lambda: {})

    def evaluate(self, _state):
        self._calls += 1
        return self._setup if self._calls == BREAKOUT_BAR + 1 else None


class _TradeManager:
    """Fills (or rejects) every open_trade() call and remembers which setups it was asked to open."""

    def __init__(self, status: OrderStatus = OrderStatus.FILLED) -> None:
        self.opened: list[str] = []
        self.status = status
        self.last_open_result = SimpleNamespace(comment="AutoTrading disabled by client", retcode=10027)
        self._position_sizer = None

    def open_trade(self, setup, broker):
        self.opened.append(setup.setup_id)
        return SimpleNamespace(status=self.status, fill_price=101.9, order_id=f"t{len(self.opened)}")


@pytest.fixture
def events(monkeypatch):
    """Captures the runners' trade events instead of writing them to logs/trade_events.log."""
    seen: list[str] = []
    for runner in RUNNERS:
        monkeypatch.setattr(runner, "_log_trade_event", lambda event, **_fields: seen.append(event))
    return seen


def _poll(runner, tail_bars: int, trade_manager: _TradeManager, tmp_path) -> None:
    """One Scheduled Task run: a fresh strategy replaying today's bars, as the runner does."""
    strategy = _breakout_strategy() if runner is nasdaq_runner else _ReplayingStrategy(_breakout_setup())
    runner._evaluate_for_new_trade(
        trade_manager, None, strategy, _orb_day(tail_bars), "XAUUSD", Timeframe.M1,
        runner._grace_bars("M1"),
        kill_switch_flag_path=tmp_path / "no_kill_switch.flag",
        traded_setups_path=tmp_path / "traded_setups.json",
    )


@pytest.mark.parametrize("runner", RUNNERS, ids=["breakout", "sweep"])
def test_a_setup_stopped_out_inside_the_grace_window_is_not_reopened(runner, tmp_path, events):
    """The 2026-09-11 XAUUSD case: opened, stopped at the broker, then seen again 2 and 4 bars old."""
    trade_manager = _TradeManager()

    _poll(runner, 0, trade_manager, tmp_path)   # breakout bar is newest: opens
    # The broker-side stop is hit; the next polls find no position and a setup 2 and 4 bars old.
    _poll(runner, 2, trade_manager, tmp_path)
    _poll(runner, 4, trade_manager, tmp_path)

    assert len(trade_manager.opened) == 1
    assert events.count("setup_already_traded") == 2


@pytest.mark.parametrize("runner", RUNNERS, ids=["breakout", "sweep"])
def test_an_untraded_setup_inside_the_window_still_opens(runner, tmp_path, events):
    """The guard must not undo the grace-window fix: first seeing a breakout 3 bars late still trades it."""
    trade_manager = _TradeManager()

    _poll(runner, 3, trade_manager, tmp_path)

    assert len(trade_manager.opened) == 1
    assert already_traded(tmp_path / "traded_setups.json", trade_manager.opened[0])


@pytest.mark.parametrize("runner", RUNNERS, ids=["breakout", "sweep"])
def test_a_rejected_open_is_not_recorded_so_the_next_poll_can_retry(runner, tmp_path, events):
    """A broker rejection opened nothing, so there is no second trade to prevent."""
    trade_manager = _TradeManager(status=OrderStatus.REJECTED)

    _poll(runner, 0, trade_manager, tmp_path)
    _poll(runner, 2, trade_manager, tmp_path)

    assert len(trade_manager.opened) == 2
    assert "setup_already_traded" not in events


@pytest.mark.parametrize("runner", RUNNERS, ids=["breakout", "sweep"])
def test_paper_and_demo_twins_keep_separate_ledgers(runner, tmp_path):
    """Both twins legitimately open the same setup id; a shared ledger would block the second."""
    demo = runner._traded_setups_path(tmp_path, "xauusd", paper=False)
    paper = runner._traded_setups_path(tmp_path, "xauusd", paper=True)

    assert demo != paper
    assert demo.parent == paper.parent == tmp_path


def test_missing_ledger_means_nothing_was_traded(tmp_path):
    """A bot's first run has no ledger file yet."""
    assert not already_traded(tmp_path / "missing.json", "setup_x")


def test_corrupt_ledger_fails_open_and_is_rewritten(tmp_path):
    """An unreadable ledger must not block every signal; it degrades to the old behaviour."""
    ledger = tmp_path / "ledger.json"
    ledger.write_text("{not json")

    assert not already_traded(ledger, "setup_x")
    record_traded(ledger, "setup_x")
    assert already_traded(ledger, "setup_x")


def test_ledger_keeps_only_the_most_recent_setups(tmp_path):
    """Setup ids carry their own date, so old entries can simply fall off."""
    ledger = tmp_path / "ledger.json"
    for i in range(MAX_REMEMBERED + 5):
        record_traded(ledger, f"setup_{i}")

    assert not already_traded(ledger, "setup_0")
    assert already_traded(ledger, f"setup_{MAX_REMEMBERED + 4}")
