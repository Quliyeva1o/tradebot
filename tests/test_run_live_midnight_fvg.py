"""Regression tests for run_live_midnight_fvg.py's daily-resolved cache.

Covers a bug found by a full-repo critical-bug audit (2026-08-26), introduced
by commit b33a42e the same session: the daily-resolved cache was written
from strategy._trade_taken, which is set True as soon as a setup is
*proposed* -- before the broker fill / kill-switch gate. A rejected order or
a kill-switch block therefore got cached as "resolved", and the cache-hit
path returned before ever reaching the open-position check, silently
starving the rest of the NY session of both retries and open-position
management. Fixed by:
  - _evaluate_for_new_trade() now returns True only on an actual FILLED order.
  - run_once()'s cache is keyed off that return value, not _trade_taken.
  - The cache-hit branch still calls get_open_positions() (and manages any
    open position found) instead of returning unconditionally.
"""

import json
import logging
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest

import risk.kill_switch as kill_switch_module
import run_live_midnight_fvg
from core.models import Bar, OrderType, SignalDirection, Timeframe
from execution.models import OrderResult, Position
from execution.trade_manager import TradeManager
from strategy.models import TradeSetup

NY = ZoneInfo("UTC")  # tests only care about relative ordering, not real NY offset


@pytest.fixture(autouse=True)
def _isolated_kill_switch_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """is_trading_halted(None) falls back to the real shared
    risk/kill_switch.flag -- tests that pass no kill_switch_flag_path (i.e.
    every run_once() call here) must not read or depend on that real file.
    """
    monkeypatch.setattr(kill_switch_module, "KILL_SWITCH_FLAG", tmp_path / "kill_switch.flag")


def _bar(minute: int, price: float = 100.0) -> Bar:
    return Bar(
        timestamp=datetime(2026, 1, 10, 0, minute, tzinfo=NY),
        open=price, high=price + 1, low=price - 1, close=price, volume=10.0,
    )


def _setup(ts: datetime, direction: SignalDirection = SignalDirection.BUY) -> TradeSetup:
    return TradeSetup(
        setup_id="setup_test",
        symbol="NAS100",
        timeframe=Timeframe.M1,
        direction=direction,
        entry_zone=(100.0, 100.0),
        stop_zone=(95.0, 95.0),
        target_zone=(112.5, 112.5),
        confidence_score=1.0,
        confluence=[],
        trigger_reason="test",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=ts,
    )


class _FakeStrategy:
    """Minimal TradeSetupStrategy stub: proposes `setup` only when the bar
    being evaluated is the last bar of the replay AND matches `signal_ts`,
    mirroring MidnightFvgStrategy's real _trade_taken-on-proposal behavior.
    """

    def __init__(self, setup: TradeSetup | None, signal_ts: datetime | None = None,
                 fvg_found: bool = True, current_date: date | None = None,
                 session_end: time = time(0, 30)) -> None:
        self._setup = setup
        self._signal_ts = signal_ts or (setup.timestamp if setup else None)
        self._trade_taken = False
        self._fvg_found = fvg_found
        self._current_date = current_date
        self.config = SimpleNamespace(session_end=session_end)
        self.diagnostics = SimpleNamespace(summary=lambda: {})

    def evaluate(self, market_state):
        bar = market_state.get_latest_bar()
        if self._setup is not None and bar.timestamp == self._signal_ts:
            self._trade_taken = True
            return self._setup
        return None

    def reset(self) -> None:
        self._trade_taken = False

    def recommended_max_holding_bars(self, timeframe: Timeframe) -> int | None:
        return None


def _open_position(opened_at: datetime | None = None, comment: str | None = None) -> Position:
    """Defaults to a position this strategy OWNS (its comment carries the
    setup_id prefix run_once() matches on -- see _partition_positions).
    """
    return Position(
        id="pos1", symbol="NAS100", order_type=OrderType.BUY_MARKET, volume=0.1,
        open_price=100.0, current_price=101.0, stop_loss=95.0, take_profit=112.5,
        timestamp=opened_at or datetime(2026, 1, 10, 0, 0, tzinfo=NY),
        comment=comment if comment is not None else f"{run_live_midnight_fvg.STRATEGY_TAG}_NAS100_M1_BUY",
    )


class TestEvaluateForNewTradeReturnValue:
    """_evaluate_for_new_trade must report True only on an actual fill."""

    def test_returns_true_when_order_fills(self) -> None:
        bars = [_bar(0), _bar(1)]
        setup = _setup(bars[-1].timestamp)
        strategy = _FakeStrategy(setup)
        broker = Mock()
        broker.place_order.return_value = OrderResult(success=True, order_id="1", position_id="p1", price=100.0, volume=0.1)
        trade_manager = TradeManager(volume=0.1)

        filled = run_live_midnight_fvg._evaluate_for_new_trade(
            trade_manager, broker, strategy, bars, "NAS100", Timeframe.M1,
        )
        assert filled is True

    def test_returns_false_when_broker_rejects_order(self) -> None:
        bars = [_bar(0), _bar(1)]
        setup = _setup(bars[-1].timestamp)
        strategy = _FakeStrategy(setup)
        broker = Mock()
        broker.place_order.return_value = OrderResult(success=False, retcode=10017, comment="Trade disabled")
        trade_manager = TradeManager(volume=0.1)

        filled = run_live_midnight_fvg._evaluate_for_new_trade(
            trade_manager, broker, strategy, bars, "NAS100", Timeframe.M1,
        )
        assert filled is False
        # The strategy still recorded the proposal -- proving _trade_taken
        # alone is NOT a safe proxy for "a trade actually exists".
        assert strategy._trade_taken is True

    def test_returns_false_when_kill_switch_blocks(self, tmp_path: Path) -> None:
        bars = [_bar(0), _bar(1)]
        setup = _setup(bars[-1].timestamp)
        strategy = _FakeStrategy(setup)
        broker = Mock()
        trade_manager = TradeManager(volume=0.1)
        flag = tmp_path / "kill_switch.flag"
        flag.write_text("halted")

        filled = run_live_midnight_fvg._evaluate_for_new_trade(
            trade_manager, broker, strategy, bars, "NAS100", Timeframe.M1,
            kill_switch_flag_path=flag,
        )
        assert filled is False
        broker.place_order.assert_not_called()
        assert strategy._trade_taken is True


class TestRunOnceDailyResolvedCache:
    def _connector(self, bars: list[Bar]) -> Mock:
        connector = Mock()
        connector.fetch_recent_bars.return_value = bars
        return connector

    def test_rejected_order_does_not_cache_today_as_resolved(self, tmp_path: Path) -> None:
        bars = [_bar(0), _bar(1)]
        setup = _setup(bars[-1].timestamp)
        strategy = _FakeStrategy(setup, current_date=bars[-1].timestamp.date())
        connector = self._connector(bars)
        broker = Mock()
        broker.get_open_positions.return_value = []
        broker.place_order.return_value = OrderResult(success=False, retcode=10017, comment="Trade disabled")
        trade_manager = TradeManager(volume=0.1)
        state_path = tmp_path / "resolved.json"

        run_live_midnight_fvg.run_once(
            connector=connector, broker=broker, trade_manager=trade_manager, strategy=strategy,
            symbol="NAS100", timeframe=Timeframe.M1, timeframe_str="M1", lookback_days=1,
            daily_resolved_state_path=state_path,
        )

        written = json.loads(state_path.read_text())
        assert written["resolved"] is False, (
            "a rejected order must not mark the day resolved -- doing so "
            "silently blocks all retries and open-position management for "
            "the rest of the NY session"
        )
        assert run_live_midnight_fvg._read_daily_resolved_date(state_path) is None

    def test_cache_hit_still_manages_an_open_position(self, tmp_path: Path) -> None:
        today = datetime.now(run_live_midnight_fvg.NY).date()
        state_path = tmp_path / "resolved.json"
        run_live_midnight_fvg._write_daily_resolved_state(state_path, today, resolved=True)

        latest_bar = _bar(5)
        connector = Mock()
        connector.fetch_recent_bars.return_value = [latest_bar]
        broker = Mock()
        broker.get_open_positions.return_value = [_open_position()]
        broker.close_position.return_value = OrderResult(success=True)
        strategy = _FakeStrategy(None, fvg_found=True, current_date=today)
        trade_manager = TradeManager(volume=0.1)

        run_live_midnight_fvg.run_once(
            connector=connector, broker=broker, trade_manager=trade_manager, strategy=strategy,
            symbol="NAS100", timeframe=Timeframe.M1, timeframe_str="M1", lookback_days=4,
            daily_resolved_state_path=state_path,
        )

        # Must fetch only a cheap bounded window, never the full lookback replay.
        connector.fetch_recent_bars.assert_called_once_with("NAS100", "M1", 60)
        # The open position must still have been attached/managed this
        # invocation -- a cache hit must never silently skip it.
        assert trade_manager.has_open_trade is True

    def test_cache_hit_with_no_open_position_skips_the_expensive_fetch(self, tmp_path: Path) -> None:
        today = datetime.now(run_live_midnight_fvg.NY).date()
        state_path = tmp_path / "resolved.json"
        run_live_midnight_fvg._write_daily_resolved_state(state_path, today, resolved=True)

        connector = Mock()
        broker = Mock()
        broker.get_open_positions.return_value = []
        strategy = _FakeStrategy(None, fvg_found=True, current_date=today)
        trade_manager = TradeManager(volume=0.1)

        run_live_midnight_fvg.run_once(
            connector=connector, broker=broker, trade_manager=trade_manager, strategy=strategy,
            symbol="NAS100", timeframe=Timeframe.M1, timeframe_str="M1", lookback_days=4,
            daily_resolved_state_path=state_path,
        )

        connector.fetch_recent_bars.assert_not_called()


class TestManageOpenTradeMultiBarGap:
    """Regression coverage for the multi-bar-gap fix: _manage_open_trade()
    used to check only bars[-1], so an SL/TP touch on an INTERMEDIATE bar
    (closed since the position opened, but not the newest one) went
    completely undetected. Real risk mainly in PAPER mode, where
    PaperBroker has no real broker-side SL/TP and relies entirely on this
    check to simulate fills.
    """

    def test_sl_touch_on_an_earlier_bar_is_not_missed_by_a_later_recovery(self, caplog) -> None:
        position = _open_position(opened_at=datetime(2026, 1, 10, 0, 0, tzinfo=NY))
        broker = Mock()
        broker.close_position.return_value = OrderResult(success=True)
        trade_manager = TradeManager(volume=0.1)

        # Position: BUY, SL=95, TP=112.5 (see _open_position()'s defaults).
        # Bar 1 (closed right after entry) touches SL; bar 2 (the NEWEST
        # bar at invocation time) recovers all the way past TP. The old
        # bars[-1]-only check would see only bar 2 and wrongly report a
        # TP win; the fix must catch bar 1's SL touch first.
        sl_touch_bar = Bar(timestamp=datetime(2026, 1, 10, 0, 1, tzinfo=NY), open=100, high=101, low=94, close=96, volume=10.0)
        recovery_bar = Bar(timestamp=datetime(2026, 1, 10, 0, 2, tzinfo=NY), open=96, high=113, low=98, close=112, volume=10.0)

        with caplog.at_level(logging.INFO, logger="run_live_midnight_fvg"):
            run_live_midnight_fvg._manage_open_trade(trade_manager, broker, position, [sl_touch_bar, recovery_bar])

        assert "closed: CLOSED_SL" in caplog.text
        assert "CLOSED_TP" not in caplog.text
        assert trade_manager.has_open_trade is False

    def test_bars_before_the_position_opened_are_ignored(self) -> None:
        """A stale bar from before entry must never be treated as a live check."""
        position = _open_position(opened_at=datetime(2026, 1, 10, 0, 5, tzinfo=NY))
        broker = Mock()
        trade_manager = TradeManager(volume=0.1)

        # This bar predates the position and would (wrongly) look like an
        # SL touch if it were checked -- it must be filtered out entirely.
        stale_bar = Bar(timestamp=datetime(2026, 1, 10, 0, 0, tzinfo=NY), open=100, high=101, low=90, close=96, volume=10.0)
        held_bar = Bar(timestamp=datetime(2026, 1, 10, 0, 6, tzinfo=NY), open=100, high=101, low=99, close=100, volume=10.0)

        run_live_midnight_fvg._manage_open_trade(trade_manager, broker, position, [stale_bar, held_bar])

        broker.close_position.assert_not_called()
        assert trade_manager.has_open_trade is True
