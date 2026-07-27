"""Tests for run_live_demo.py, the Sprint 7 live trading loop.

Wires live_signal_check's detection into TradeManager, driven by an IBroker.
Full-loop simulation tests drive run_once() directly against PaperBroker
(never real MT5) -- each simulated "run" constructs a FRESH TradeManager,
exactly mirroring a separate Task Scheduler process invocation, so these
tests prove the broker-is-ground-truth state-continuity design (see
run_live_demo._attach_to_open_position()) actually works across runs, not
just within one.
"""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import Mock, patch

import pytest

import execution.paper_broker as paper_broker_module
import live_signal_check
import risk.daily_risk_tracker as daily_risk_tracker_module
import risk.kill_switch as kill_switch_module
import run_live_demo
from core.models import AccountInfo, Bar, OrderType, SignalDirection, Timeframe
from execution.models import Position
from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager
from mt5.connector import MT5Connector
from risk.daily_risk_tracker import DailyRiskTracker
from strategy.nasdaq_midline_sweep import NasdaqMidlineSweepStrategy


@pytest.fixture(autouse=True)
def _isolated_paper_broker_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paper_broker_module, "STATE_FILE", tmp_path / "paper_broker_state.json")


@pytest.fixture(autouse=True)
def _isolated_kill_switch_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kill_switch_module, "KILL_SWITCH_FLAG", tmp_path / "kill_switch.flag")


@pytest.fixture(autouse=True)
def _isolated_daily_risk_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_risk_tracker_module, "STATE_FILE", tmp_path / "daily_risk_state.json")


@pytest.fixture(autouse=True)
def _isolated_data_quality_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolates Sprint 7's data-quality alert dedup state (mirrors test_live_signal_check.py).

    run_once() reuses live_signal_check.check_data_quality_and_alert()
    unchanged, which reads/writes DATA_QUALITY_STATE_FILE on every call --
    without this, every test here would leak alerted-signature state into
    the real repo-relative logs/last_data_quality_alerts.json.
    """
    monkeypatch.setattr(
        live_signal_check, "DATA_QUALITY_STATE_FILE", tmp_path / "last_data_quality_alerts.json"
    )


@pytest.fixture(autouse=True)
def _mock_telegram_alerts() -> Iterator[None]:
    """Prevents any real Telegram network call across this whole file.

    run_live_demo.send_telegram_alert (signal alerts) is imported by name
    into run_live_demo's own namespace, so patched there per unittest.mock's
    patch-where-it's-used rule. live_signal_check.send_data_quality_alert
    (T3 data-quality alerts, Sprint 6c) is called unqualified from inside
    live_signal_check.check_data_quality_and_alert -- which run_live_demo
    also reuses unchanged -- so it must be patched on live_signal_check's
    own namespace instead, mirroring test_live_signal_check.py's identical
    fixture.
    """
    with (
        patch("run_live_demo.send_telegram_alert"),
        patch("live_signal_check.send_data_quality_alert"),
    ):
        yield


def _bar(ts: datetime, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(timestamp=ts, open=o, high=h, low=low, close=c, volume=100.0)


# Reuses the exact bar shapes from tests/test_live_signal_check.py's
# TestCheckSignal.test_finds_signal_on_final_bar (sma_period=5,
# session_timezone="UTC" strategy config) -- a known-good BUY signal with
# entry_zone=(112,112), stop_zone=(90,90), target_zone=(156,156).
def _build_session_bars() -> list[Bar]:
    base = datetime(2026, 1, 5, 9, 30, tzinfo=UTC)
    return [
        _bar(base, 100.0, 100.6, 99.9, 100.5),
        _bar(base + timedelta(minutes=5), 100.0, 100.1, 99.4, 99.5),
        _bar(base + timedelta(minutes=10), 100.0, 100.6, 99.9, 100.5),
        _bar(base + timedelta(minutes=15), 100.0, 100.1, 99.4, 99.5),
    ]


def _session_end_bar() -> Bar:
    return _bar(datetime(2026, 1, 5, 9, 50, tzinfo=UTC), 100.0, 100.6, 99.9, 100.5)


def _breakout_bar() -> Bar:
    return _bar(datetime(2026, 1, 5, 10, 0, tzinfo=UTC), 105.0, 112.5, 108.0, 112.0)


def _neutral_bar(ts: datetime = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)) -> Bar:
    """A bar shaped to never fire a signal (flat, no sweep/displacement)."""
    return _bar(ts, 100.0, 100.2, 99.8, 100.0)


def _price_bar(low: float, high: float, ts: datetime = datetime(2026, 1, 5, 11, 0, tzinfo=UTC)) -> Bar:
    """A bar shaped for use as a TradeManager.on_new_bar() price check (high/low matter)."""
    return _bar(ts, (low + high) / 2, high, low, (low + high) / 2)


def _strategy() -> NasdaqMidlineSweepStrategy:
    return NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")


def _fake_connector(tick_histories: list[list[Bar]]) -> Mock:
    """A Mock(spec=MT5Connector) yielding one canned bar-history per tick.

    Mirrors tests/test_trade_manager.py's `_broker()` helper's use of
    Mock(spec=MT5Connector) rather than a hand-written duck-typed stand-in,
    so this satisfies PaperBroker/run_once()'s `MT5Connector`-typed
    parameters under mypy while still letting a test fully control what
    fetch_recent_bars() returns.

    fetch_recent_bars() is called with TWO distinct purposes within a single
    tick: once by run_once() itself (count=lookback_bars, wants the full
    history for signal detection) and, if PaperBroker fills/marks-to-market,
    again internally (count=1, wants only the single latest reference bar).
    Only a count!=1 call advances to the next tick's canned history; a
    count=1 call always reuses the CURRENT tick's last bar, so PaperBroker's
    fill/mark-to-market price is consistent with what run_once() itself just
    evaluated -- exactly mirrors real MT5 behavior (the "latest bar" doesn't
    change mid-tick).
    """
    connector = Mock(spec=MT5Connector)
    connector.connect.return_value = True
    current = tick_histories[0]
    next_index = 0

    def _fetch_recent_bars(symbol: str, timeframe: str, count: int) -> list[Bar]:
        nonlocal current, next_index
        if count == 1:
            return [current[-1]]
        index = min(next_index, len(tick_histories) - 1)
        current = tick_histories[index]
        next_index += 1
        return current

    connector.fetch_recent_bars.side_effect = _fetch_recent_bars
    return connector


def _tick(fake_connector: Mock, symbol: str = "USTEC", volume: float = 0.1) -> None:
    """Simulates exactly one fresh Task Scheduler invocation's worth of work.

    Constructs a brand-new PaperBroker (loading whatever state the PREVIOUS
    tick persisted -- see execution/paper_broker.py) and a brand-new
    TradeManager (which, unlike PaperBroker, has no persistence of its own
    -- see run_live_demo._attach_to_open_position()'s docstring) each call,
    exactly mirroring separate process invocations.
    """
    broker = PaperBroker(connector=fake_connector, slippage=0.0, timeframe="M5")
    trade_manager = TradeManager(volume=volume)
    run_live_demo.run_once(
        connector=fake_connector,
        broker=broker,
        trade_manager=trade_manager,
        strategy=_strategy(),
        symbol=symbol,
        timeframe=Timeframe.M5,
        timeframe_str="M5",
        lookback_bars=1000,
    )


def _current_broker() -> PaperBroker:
    """A fresh PaperBroker reading whatever state is currently persisted (read-only helper for assertions)."""
    connector = Mock(spec=MT5Connector)
    connector.fetch_recent_bars.return_value = [_neutral_bar()]
    return PaperBroker(connector=connector, slippage=0.0, timeframe="M5")


class TestNoSignalNoOp:
    """No signal, no open trade -> a true no-op, matching live_signal_check.py."""

    def test_no_signal_is_a_true_no_op(self) -> None:
        connector = _fake_connector([[*_build_session_bars(), _session_end_bar(), _neutral_bar()]])

        _tick(connector)

        assert _current_broker().get_open_positions() == []


class TestSignalFiresAndOpensTrade:
    """No open trade, a signal fires -> TradeManager.open_trade() is called."""

    def test_signal_opens_a_tracked_position(self) -> None:
        connector = _fake_connector(
            [[*_build_session_bars(), _session_end_bar(), _breakout_bar()]]
        )

        _tick(connector)

        positions = _current_broker().get_open_positions()
        assert len(positions) == 1
        assert positions[0].symbol == "USTEC"
        assert positions[0].order_type.name == "BUY_MARKET"
        assert positions[0].stop_loss == pytest.approx(90.0)
        assert positions[0].take_profit == pytest.approx(156.0)

    def test_second_tick_does_not_open_a_second_position(self) -> None:
        """A trade is already open -> the next tick must manage it, not re-evaluate for a new signal."""
        connector = _fake_connector(
            [
                [*_build_session_bars(), _session_end_bar(), _breakout_bar()],
                [_price_bar(low=100.0, high=110.0)],
            ]
        )

        _tick(connector)
        _tick(connector)

        assert len(_current_broker().get_open_positions()) == 1

    def test_a_successful_open_explicitly_confirms_the_trade_taken_guard(self) -> None:
        """Sprint 8: after a REAL open_trade() success, run_live_demo.py must
        explicitly confirm the day's one-trade guard as consumed via
        strategy.mark_trade_taken() -- not rely solely on the final bar's
        own evaluate() call (which already sets it automatically for a
        found setup, independent of whether the broker actually fills it).
        Verified directly via the real method, wrapped to record calls
        rather than a bare state check, since _trade_taken would already be
        True from the final-bar evaluation regardless of this call.
        """
        connector = _fake_connector([[*_build_session_bars(), _session_end_bar(), _breakout_bar()]])
        broker = PaperBroker(connector=connector, slippage=0.0, timeframe="M5")
        trade_manager = TradeManager(volume=0.1)
        strategy = _strategy()

        with patch.object(
            strategy, "mark_trade_taken", wraps=strategy.mark_trade_taken
        ) as mock_mark_taken:
            run_live_demo.run_once(
                connector=connector,
                broker=broker,
                trade_manager=trade_manager,
                strategy=strategy,
                symbol="USTEC",
                timeframe=Timeframe.M5,
                timeframe_str="M5",
                lookback_bars=1000,
            )

        mock_mark_taken.assert_called_once()
        assert strategy._trade_taken is True

    def test_a_rejected_open_does_not_call_mark_trade_taken(self) -> None:
        connector = _fake_connector([[*_build_session_bars(), _session_end_bar(), _breakout_bar()]])
        broker = Mock()
        broker.get_open_positions.return_value = []
        broker.place_order.return_value = Mock(success=False, order_id="x", comment="no money")
        trade_manager = TradeManager(volume=0.1)
        strategy = _strategy()

        with patch.object(
            strategy, "mark_trade_taken", wraps=strategy.mark_trade_taken
        ) as mock_mark_taken:
            run_live_demo.run_once(
                connector=connector,
                broker=broker,
                trade_manager=trade_manager,
                strategy=strategy,
                symbol="USTEC",
                timeframe=Timeframe.M5,
                timeframe_str="M5",
                lookback_bars=1000,
            )

        mock_mark_taken.assert_not_called()


class TestTradeHoldsAcrossRuns:
    """An open trade with price inside SL/TP stays open across several separate ticks."""

    def test_holds_while_price_stays_between_sl_and_tp(self) -> None:
        connector = _fake_connector(
            [
                [*_build_session_bars(), _session_end_bar(), _breakout_bar()],
                [_price_bar(low=100.0, high=110.0)],
                [_price_bar(low=95.0, high=115.0)],
                [_price_bar(low=110.0, high=140.0)],
            ]
        )

        for _ in range(4):
            _tick(connector)

        positions = _current_broker().get_open_positions()
        assert len(positions) == 1
        assert positions[0].stop_loss == pytest.approx(90.0)
        assert positions[0].take_profit == pytest.approx(156.0)


class TestTradeClosesOnSL:
    """A later tick whose bar hits the tracked stop-loss closes the position."""

    def test_sl_hit_closes_the_position(self) -> None:
        connector = _fake_connector(
            [
                [*_build_session_bars(), _session_end_bar(), _breakout_bar()],
                [_price_bar(low=100.0, high=110.0)],
                [_price_bar(low=85.0, high=95.0)],  # low <= stop_loss (90.0)
            ]
        )

        _tick(connector)
        _tick(connector)
        _tick(connector)

        assert _current_broker().get_open_positions() == []


class TestTradeClosesOnTP:
    """A later tick whose bar hits the tracked take-profit closes the position."""

    def test_tp_hit_closes_the_position(self) -> None:
        connector = _fake_connector(
            [
                [*_build_session_bars(), _session_end_bar(), _breakout_bar()],
                [_price_bar(low=100.0, high=110.0)],
                [_price_bar(low=150.0, high=160.0)],  # high >= take_profit (156.0)
            ]
        )

        _tick(connector)
        _tick(connector)
        _tick(connector)

        assert _current_broker().get_open_positions() == []


class TestAmbiguousOpenPositions:
    """More than one open position for the symbol is an unexpected, fail-safe no-op."""

    def test_more_than_one_matching_open_position_is_a_safe_no_op(self) -> None:
        connector = _fake_connector([[_neutral_bar()]])
        broker = Mock()
        broker.get_open_positions.return_value = [
            Mock(symbol="USTEC", id="a"),
            Mock(symbol="USTEC", id="b"),
        ]
        trade_manager = TradeManager()

        run_live_demo.run_once(
            connector=connector,
            broker=broker,
            trade_manager=trade_manager,
            strategy=_strategy(),
            symbol="USTEC",
            timeframe=Timeframe.M5,
            timeframe_str="M5",
            lookback_bars=1000,
        )

        broker.place_order.assert_not_called()
        broker.close_position.assert_not_called()


class TestDemoAccountSafetyRail:
    """Isolated tests for the two-layer demo-account safety rail.

    This is main()'s responsibility, not run_once()'s -- see run_live_demo.py
    module docstring.
    """

    def test_missing_mt5_account_type_config_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_settings = Mock(MT5_ACCOUNT_TYPE="")
        monkeypatch.setattr(run_live_demo.Settings, "load", classmethod(lambda cls: fake_settings))

        with pytest.raises(run_live_demo.DemoAccountRequiredError, match="MT5_ACCOUNT_TYPE"):
            run_live_demo._ensure_explicit_demo_configuration()

    def test_live_mt5_account_type_config_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_settings = Mock(MT5_ACCOUNT_TYPE="live")
        monkeypatch.setattr(run_live_demo.Settings, "load", classmethod(lambda cls: fake_settings))

        with pytest.raises(run_live_demo.DemoAccountRequiredError):
            run_live_demo._ensure_explicit_demo_configuration()

    def test_demo_mt5_account_type_config_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_settings = Mock(MT5_ACCOUNT_TYPE="  Demo  ")  # case/whitespace-insensitive
        monkeypatch.setattr(run_live_demo.Settings, "load", classmethod(lambda cls: fake_settings))

        run_live_demo._ensure_explicit_demo_configuration()  # must not raise

    def test_mocked_real_account_trade_mode_raises(self) -> None:
        """The MT5-verified layer must block/fail loudly on a REAL account.

        Independent of whatever the .env-level config claims.
        """
        real_account_info = AccountInfo(
            balance=10_000.0, equity=10_000.0, margin=0.0, free_margin=10_000.0, trade_mode=2
        )

        with pytest.raises(run_live_demo.DemoAccountRequiredError, match="LIVE"):
            run_live_demo._ensure_demo_trade_mode(real_account_info)

    def test_mocked_unknown_trade_mode_also_raises(self) -> None:
        """Don't default silently.

        An account response that doesn't expose trade_mode at all is also
        treated as unsafe, not waved through.
        """
        unknown_account_info = AccountInfo(
            balance=10_000.0, equity=10_000.0, margin=0.0, free_margin=10_000.0, trade_mode=None
        )

        with pytest.raises(run_live_demo.DemoAccountRequiredError):
            run_live_demo._ensure_demo_trade_mode(unknown_account_info)

    def test_mocked_demo_account_trade_mode_passes(self) -> None:
        demo_account_info = AccountInfo(
            balance=10_000.0, equity=10_000.0, margin=0.0, free_margin=10_000.0, trade_mode=0
        )

        run_live_demo._ensure_demo_trade_mode(demo_account_info)  # must not raise


class TestKillSwitchAndDailyRiskGating:
    """Proves kill_switch.py / daily_risk_tracker.py actually prevent order placement.

    Not just log -- the Sprint 7 requirement. run_once()/_evaluate_for_new_trade()
    read risk.kill_switch.is_trading_halted() directly, so writing the real
    flag file (via the isolated KILL_SWITCH_FLAG fixture) exercises the real
    integration, not a mocked stand-in.
    """

    def test_active_kill_switch_blocks_a_firing_signal_from_opening_a_trade(self) -> None:
        # Writes the flag directly (mirrors test_kill_switch.py's
        # test_true_when_flag_exists) -- this test is about run_once()
        # respecting an active kill-switch, not about activate_kill_switch()'s
        # own Telegram-alerting behavior (covered in test_kill_switch.py).
        kill_switch_module.KILL_SWITCH_FLAG.write_text("test: manual halt")
        connector = _fake_connector([[*_build_session_bars(), _session_end_bar(), _breakout_bar()]])

        _tick(connector)

        assert _current_broker().get_open_positions() == []

    def test_daily_risk_breach_activates_kill_switch_and_then_blocks_the_next_signal(self) -> None:
        # Day-start baseline: 10,000. A later equity read of 9,000 is a 10%
        # loss, breaching the default 5% MAX_DAILY_LOSS_PCT -- mirrors
        # main()'s own call sequence (fetch account_info, then
        # DailyRiskTracker().check_and_update(equity)) before run_once().
        tracker = DailyRiskTracker(max_daily_loss_pct=0.05)
        tracker.check_and_update(10_000.0)  # records today's baseline
        with patch("risk.kill_switch.TelegramNotifier"):
            newly_halted = tracker.check_and_update(9_000.0)  # breaches -> activates kill-switch
        assert newly_halted is True
        assert kill_switch_module.is_trading_halted() is True

        connector = _fake_connector([[*_build_session_bars(), _session_end_bar(), _breakout_bar()]])
        _tick(connector)

        assert _current_broker().get_open_positions() == []

    def test_open_trade_management_is_unaffected_by_run_once_itself_seeing_kill_switch(self) -> None:
        """run_once() delegates the kill-switch gate entirely to _evaluate_for_new_trade().

        That path is only reached when no position is open; managing an
        ALREADY-open position never consults it -- see run_live_demo.py
        module docstring for why that split is main()'s job, not
        run_once()'s, when driven through the real CLI entry point. This
        test only proves run_once() itself never blocks position management
        on kill-switch state, since it never checks it in that branch.
        """
        connector = _fake_connector(
            [
                [*_build_session_bars(), _session_end_bar(), _breakout_bar()],
                [_price_bar(low=100.0, high=110.0)],
            ]
        )
        _tick(connector)
        kill_switch_module.KILL_SWITCH_FLAG.write_text("test: halt after opening")

        _tick(connector)

        assert len(_current_broker().get_open_positions()) == 1


class TestAttachToOpenPosition:
    """Unit tests for the state-continuity rehydration helper itself."""

    def test_rehydrates_all_tracked_fields_from_a_position(self) -> None:
        broker = Mock()
        position = Position(
            id="pos-1",
            symbol="USTEC",
            order_type=OrderType.SELL_MARKET,
            volume=0.1,
            open_price=100.0,
            current_price=99.0,
            stop_loss=105.0,
            take_profit=90.0,
        )
        trade_manager = TradeManager()

        run_live_demo._attach_to_open_position(trade_manager, broker, position)

        assert trade_manager.has_open_trade is True
        assert trade_manager._broker is broker
        assert trade_manager._position_id == "pos-1"
        assert trade_manager._direction == SignalDirection.SELL
        assert trade_manager._stop_loss == 105.0
        assert trade_manager._take_profit == 90.0


class TestUnmanageablePosition:
    """A position lacking SL/TP is logged and skipped, never crashes the tick."""

    def test_position_missing_sl_or_tp_is_skipped_not_crashed(self) -> None:
        connector = _fake_connector([[_neutral_bar()]])
        broker = Mock()
        broker.get_open_positions.return_value = [
            Position(
                id="pos-1",
                symbol="USTEC",
                order_type=OrderType.BUY_MARKET,
                volume=0.1,
                open_price=100.0,
                current_price=100.0,
                stop_loss=None,
                take_profit=None,
            )
        ]
        trade_manager = TradeManager()

        run_live_demo.run_once(
            connector=connector,
            broker=broker,
            trade_manager=trade_manager,
            strategy=_strategy(),
            symbol="USTEC",
            timeframe=Timeframe.M5,
            timeframe_str="M5",
            lookback_bars=1000,
        )

        broker.close_position.assert_not_called()
        assert trade_manager.has_open_trade is False


class TestManageOpenTradeCloseFailure:
    """Tests for _manage_open_trade()'s handling of a declined close.

    A broker-declined close must produce a distinct, loud log line and
    trade_events.log event ("close_failed") instead of the misleading
    "closed" event/log line -- and must not stop the position from being
    retried on a later tick.
    """

    def _position(self, **overrides: object) -> Position:
        defaults: dict[str, object] = {
            "id": "pos-1",
            "symbol": "USTEC",
            "order_type": OrderType.BUY_MARKET,
            "volume": 0.1,
            "open_price": 100.0,
            "current_price": 95.0,
            "stop_loss": 90.0,
            "take_profit": 156.0,
        }
        defaults.update(overrides)
        return Position(**defaults)  # type: ignore[arg-type]

    def _failing_close_result(self) -> Mock:
        return Mock(
            success=False, retcode=10018, comment="Market closed", order_id="", position_id="pos-1"
        )

    def test_failed_close_logs_a_distinct_failed_to_close_line(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        broker = Mock()
        broker.close_position.return_value = self._failing_close_result()
        trade_manager = TradeManager()
        position = self._position()
        final_bar = _price_bar(low=85.0, high=95.0)  # low <= stop_loss (90.0)

        with caplog.at_level(logging.ERROR, logger="run_live_demo"):
            run_live_demo._manage_open_trade(trade_manager, broker, position, final_bar)

        assert "FAILED TO CLOSE" in caplog.text
        assert "Market closed" in caplog.text

    def test_failed_close_emits_a_close_failed_trade_event_not_closed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        broker = Mock()
        broker.close_position.return_value = self._failing_close_result()
        trade_manager = TradeManager()
        position = self._position()
        final_bar = _price_bar(low=85.0, high=95.0)

        with caplog.at_level(logging.INFO, logger="trade_events"):
            run_live_demo._manage_open_trade(trade_manager, broker, position, final_bar)

        events = [r for r in caplog.records if r.name == "trade_events"]
        assert len(events) == 1
        payload = cast("dict[str, object]", events[0].msg)
        assert payload["event_type"] == "close_failed"
        assert payload["symbol"] == "USTEC"
        assert payload["position_id"] == "pos-1"
        assert payload["reason"] == "Market closed"
        assert payload["retcode"] == 10018

    def test_failed_close_leaves_trade_manager_still_tracking_the_position(self) -> None:
        broker = Mock()
        broker.close_position.return_value = self._failing_close_result()
        trade_manager = TradeManager()
        position = self._position()
        final_bar = _price_bar(low=85.0, high=95.0)

        run_live_demo._manage_open_trade(trade_manager, broker, position, final_bar)

        assert trade_manager.has_open_trade is True
        assert trade_manager._position_id == "pos-1"

    def test_next_tick_retries_the_close_against_the_still_open_position(self) -> None:
        """Confirms the failed close is retried, not silently dropped, on the next tick.

        After a failed close, the position is still open on the broker (a
        rejected close changes nothing broker-side), so the next run_once()
        tick's get_open_positions() reconciliation re-finds it and retries
        the close -- the same state-continuity design
        TestTradeHoldsAcrossRuns/TestTradeClosesOnSL rely on for the happy
        path.
        """
        connector = Mock(spec=MT5Connector)
        connector.fetch_recent_bars.return_value = [_price_bar(low=85.0, high=95.0)]
        broker = Mock()
        broker.get_open_positions.return_value = [self._position()]
        broker.close_position.side_effect = [
            self._failing_close_result(),
            Mock(
                success=True,
                retcode=10009,
                comment="",
                order_id="c1",
                position_id="pos-1",
                price=90.0,
                volume=0.1,
            ),
        ]

        first_trade_manager = TradeManager()
        run_live_demo.run_once(
            connector=connector,
            broker=broker,
            trade_manager=first_trade_manager,
            strategy=_strategy(),
            symbol="USTEC",
            timeframe=Timeframe.M5,
            timeframe_str="M5",
            lookback_bars=1000,
        )

        assert first_trade_manager.has_open_trade is True
        assert broker.close_position.call_count == 1

        second_trade_manager = TradeManager()
        run_live_demo.run_once(
            connector=connector,
            broker=broker,
            trade_manager=second_trade_manager,
            strategy=_strategy(),
            symbol="USTEC",
            timeframe=Timeframe.M5,
            timeframe_str="M5",
            lookback_bars=1000,
        )

        assert broker.close_position.call_count == 2
        assert second_trade_manager.has_open_trade is False


class TestEvaluateForNewTradeOpenRejection:
    """Tests for _evaluate_for_new_trade()'s handling of a declined open.

    Regression coverage for the 2026-07-27T11:09:11Z SELL USTEC rejection:
    trade_events.log recorded a "trade_open_rejected" event with only
    symbol/setup_id/order_id -- no retcode/comment -- and neither
    MT5Broker.place_order()'s nor TradeManager.open_trade()'s own
    logger.error() calls (which DID capture the real reason) ever reached a
    persisted log file, so the actual MT5 rejection reason (eventually
    tracked down to retcode 10017, "Trade disabled") took real-account
    archaeology to recover after the fact. Both the human-readable log line
    and the structured event must now carry it directly.
    """

    def _rejecting_broker(self, retcode: int = 10017, comment: str = "Trade disabled") -> Mock:
        broker = Mock()
        broker.get_open_positions.return_value = []
        broker.place_order.return_value = Mock(
            success=False, retcode=retcode, comment=comment, order_id="0", position_id=""
        )
        return broker

    def _signal_bars(self) -> list[Bar]:
        return [*_build_session_bars(), _session_end_bar(), _breakout_bar()]

    def test_rejected_open_logs_the_reason_and_retcode(self, caplog: pytest.LogCaptureFixture) -> None:
        broker = self._rejecting_broker()
        trade_manager = TradeManager(volume=0.1)

        with caplog.at_level(logging.ERROR, logger="run_live_demo"):
            run_live_demo._evaluate_for_new_trade(
                trade_manager, broker, _strategy(), self._signal_bars(), "USTEC", Timeframe.M5
            )

        assert "REJECTED" in caplog.text
        assert "Trade disabled" in caplog.text
        assert "10017" in caplog.text

    def test_rejected_open_emits_a_trade_open_rejected_event_with_reason_and_retcode(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        broker = self._rejecting_broker()
        trade_manager = TradeManager(volume=0.1)

        with caplog.at_level(logging.INFO, logger="trade_events"):
            run_live_demo._evaluate_for_new_trade(
                trade_manager, broker, _strategy(), self._signal_bars(), "USTEC", Timeframe.M5
            )

        events = [
            r
            for r in caplog.records
            if r.name == "trade_events" and cast("dict[str, object]", r.msg)["event_type"] == "trade_open_rejected"
        ]
        assert len(events) == 1
        payload = cast("dict[str, object]", events[0].msg)
        assert payload["reason"] == "Trade disabled"
        assert payload["retcode"] == 10017

    def test_rejected_open_leaves_no_open_trade_and_records_last_open_result(self) -> None:
        broker = self._rejecting_broker()
        trade_manager = TradeManager(volume=0.1)

        run_live_demo._evaluate_for_new_trade(
            trade_manager, broker, _strategy(), self._signal_bars(), "USTEC", Timeframe.M5
        )

        assert trade_manager.has_open_trade is False
        assert trade_manager.last_open_result is not None
        assert trade_manager.last_open_result.success is False
        assert trade_manager.last_open_result.retcode == 10017
        assert trade_manager.last_open_result.comment == "Trade disabled"
