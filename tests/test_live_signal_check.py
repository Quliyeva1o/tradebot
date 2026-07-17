"""Unit tests for live_signal_check.py (read-only, one-shot Midline Sweep signal check).

No real MT5 connection or Telegram send is made anywhere in this file --
tests that reach the signal-found path always mock the Telegram layer too
(main() calls send_telegram_alert() unconditionally on a found signal, and
config.settings.Settings.load() reads whatever is actually configured in the
real .env, so leaving it unmocked here would attempt a real network call).
"""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

import live_signal_check
from core.models import Bar, SignalDirection, Timeframe
from strategy.diagnostics import RejectionReason
from strategy.models import TradeSetup
from strategy.nasdaq_midline_sweep import NasdaqMidlineSweepStrategy


@pytest.fixture(autouse=True)
def _isolated_state_file(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Points STATE_FILE at a per-test tmp_path location.

    Without this, every test that reaches main()'s signal-found path would
    read/write the real repo-relative logs/last_signal_state.json --
    polluting the working tree and leaking signatures across tests (several
    tests below intentionally reuse the identical breakout bar timestamp,
    so a real shared file would make later tests see "already sent" from an
    earlier, unrelated test run).
    """
    monkeypatch.setattr(live_signal_check, "STATE_FILE", tmp_path / "last_signal_state.json")


@pytest.fixture(autouse=True)
def _no_real_log_file():
    """Detaches live_signal_check's FileHandler for the duration of each test.

    live_signal_check.logger is a module-level singleton, configured once at
    import time with setup_logger(log_to_file=True) -- unlike STATE_FILE
    above, it can't be redirected per test (setup_logger's `if logger.handlers:
    return logger` guard means calling it again is a no-op). Without this
    fixture, every test's logger.info/warning/error calls (including the
    intentionally-simulated MT5-connect-failure, Telegram RuntimeError, and
    FileExistsError fail-open scenarios below) would land in the real
    logs/live_signal_check.log -- the exact file a human running the script
    via Task Scheduler relies on to verify it's actually working, polluted
    with confusing fake-dated test noise indistinguishable from real runs.
    """
    file_handlers = [h for h in live_signal_check.logger.handlers if isinstance(h, logging.FileHandler)]
    for h in file_handlers:
        live_signal_check.logger.removeHandler(h)
    yield
    for h in file_handlers:
        live_signal_check.logger.addHandler(h)


def _bar(ts: datetime, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(timestamp=ts, open=o, high=h, low=low, close=c, volume=100.0)


# Build-session bars (09:30-09:45 UTC, matching session_timezone="UTC" strategies
# used directly in these unit tests): closes average to exactly 100.0, each body a
# constant 0.5 (baseline for the SMA displacement filter).
def _build_session_bars(day: int = 5) -> list[Bar]:
    base = datetime(2026, 1, day, 9, 30, tzinfo=UTC)
    return [
        _bar(base, 100.0, 100.6, 99.9, 100.5),
        _bar(base + timedelta(minutes=5), 100.0, 100.1, 99.4, 99.5),
        _bar(base + timedelta(minutes=10), 100.0, 100.6, 99.9, 100.5),
        _bar(base + timedelta(minutes=15), 100.0, 100.1, 99.4, 99.5),
    ]


def _session_end_bar(day: int = 5) -> Bar:
    return _bar(datetime(2026, 1, day, 9, 50, tzinfo=UTC), 100.0, 100.6, 99.9, 100.5)


class TestCheckSignal:
    def test_raises_on_empty_bars(self) -> None:
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        with pytest.raises(ValueError, match="No bars"):
            live_signal_check.check_signal([], "USTEC", Timeframe.M5, strategy)

    def test_finds_signal_on_final_bar(self) -> None:
        """A build session + a long sweep+reclaim+displacement breakout as the
        FINAL bar must be reported as a BUY setup.
        """
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        bars = [
            *_build_session_bars(),
            _session_end_bar(),
            # low=108 < upper(110) -> sweep; close=112 > upper -> reclaim;
            # close=112 > mid+buffer(105) -> long_ok; body=7 >> avg body baseline.
            _bar(datetime(2026, 1, 5, 10, 0, tzinfo=UTC), 105.0, 112.5, 108.0, 112.0),
        ]

        setup, diagnostics, final_bar = live_signal_check.check_signal(
            bars, "USTEC", Timeframe.M5, strategy
        )

        assert setup is not None
        assert setup.direction == SignalDirection.BUY
        assert setup.entry_zone == (112.0, 112.0)
        assert setup.stop_zone == (90.0, 90.0)
        assert setup.target_zone == (156.0, 156.0)
        assert final_bar.timestamp == bars[-1].timestamp
        # Diagnostics must reflect only the final bar's single evaluation.
        assert diagnostics["0_NasdaqMidlineSweepStrategy"]["evaluations"] == 1
        assert diagnostics["0_NasdaqMidlineSweepStrategy"]["setups_generated"] == 1

    def test_no_signal_reports_isolated_final_bar_rejection_reason(self) -> None:
        """A neutral final bar (no sweep/displacement) must report NO signal, and
        the diagnostics must show ONLY the final bar's rejection -- not an
        aggregate that includes the ZONE_NOT_READY rejections from the earlier
        build-session replay bars.
        """
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        bars = [
            *_build_session_bars(),
            _session_end_bar(),
            # Close stays within mid +/- buffer (100 +/- 5): WRONG_SIDE_OF_MID.
            _bar(datetime(2026, 1, 5, 10, 0, tzinfo=UTC), 100.0, 102.0, 98.0, 102.0),
        ]

        setup, diagnostics, final_bar = live_signal_check.check_signal(
            bars, "USTEC", Timeframe.M5, strategy
        )

        assert setup is None
        summary = diagnostics["0_NasdaqMidlineSweepStrategy"]
        assert summary["evaluations"] == 1
        assert summary["rejections"] == {RejectionReason.WRONG_SIDE_OF_MID.value: 1}
        assert final_bar.close == 102.0

    def test_replay_builds_daily_state_but_only_final_bar_result_is_returned(self) -> None:
        """A signal on a bar BEFORE the final one must not be returned -- only
        the final bar's outcome matters, even though the replay evaluates every
        bar to build up the strategy's daily-scoped state.
        """
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        breakout_bar = _bar(datetime(2026, 1, 5, 10, 0, tzinfo=UTC), 105.0, 112.5, 108.0, 112.0)
        # trade_taken becomes True on this bar, then a neutral bar follows as the final one.
        final_neutral_bar = _bar(datetime(2026, 1, 5, 10, 5, tzinfo=UTC), 112.0, 113.0, 108.5, 112.5)
        bars = [*_build_session_bars(), _session_end_bar(), breakout_bar, final_neutral_bar]

        setup, diagnostics, final_bar = live_signal_check.check_signal(
            bars, "USTEC", Timeframe.M5, strategy
        )

        assert setup is None  # the breakout happened on an earlier bar, not the final one
        assert final_bar.timestamp == final_neutral_bar.timestamp
        summary = diagnostics["0_NasdaqMidlineSweepStrategy"]
        assert summary["rejections"] == {RejectionReason.TRADE_ALREADY_TAKEN.value: 1}


class TestFormatting:
    def test_format_setup_contains_key_fields(self) -> None:
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        bars = [
            *_build_session_bars(),
            _session_end_bar(),
            _bar(datetime(2026, 1, 5, 10, 0, tzinfo=UTC), 105.0, 112.5, 108.0, 112.0),
        ]
        setup, _diag, _final = live_signal_check.check_signal(bars, "USTEC", Timeframe.M5, strategy)

        text = live_signal_check.format_setup(setup)

        assert "SIGNAL FOUND" in text
        assert "BUY (LONG)" in text
        assert "112.00" in text  # entry
        assert "90.00" in text  # stop-loss
        assert "156.00" in text  # take-profit
        assert setup.trigger_reason in text

    def test_format_no_signal_contains_rejection_reason(self) -> None:
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        bars = [
            *_build_session_bars(),
            _session_end_bar(),
            _bar(datetime(2026, 1, 5, 10, 0, tzinfo=UTC), 100.0, 102.0, 98.0, 102.0),
        ]
        _setup, diagnostics, final_bar = live_signal_check.check_signal(
            bars, "USTEC", Timeframe.M5, strategy
        )

        text = live_signal_check.format_no_signal(diagnostics, final_bar)

        assert "NO SIGNAL" in text
        assert "wrong_side_of_mid" in text


class TestMainEndToEnd:
    """Exercises main() with a fully mocked MT5Connector -- no real MT5 connection."""

    def test_main_prints_signal_found_when_strategy_default_config_fires(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Uses real defaults (build_session 09:30-09:50 America/New_York,
        body_multiplier=1.5) -- bars are timestamped in UTC on a non-DST date
        (Jan 6 2026, EST = UTC-5) so 09:30-09:50 EST = 14:30-14:50 UTC.
        """
        base = datetime(2026, 1, 6, 12, 50, tzinfo=UTC)
        warmup_bars = [
            _bar(base + timedelta(minutes=5 * i), 100.0, 100.5, 99.5, 100.5 if i % 2 == 0 else 99.5)
            for i in range(20)
        ]
        build_bars = [
            _bar(datetime(2026, 1, 6, 14, 30, tzinfo=UTC), 100.0, 100.6, 99.9, 100.5),
            _bar(datetime(2026, 1, 6, 14, 35, tzinfo=UTC), 100.0, 100.1, 99.4, 99.5),
            _bar(datetime(2026, 1, 6, 14, 40, tzinfo=UTC), 100.0, 100.6, 99.9, 100.5),
            _bar(datetime(2026, 1, 6, 14, 45, tzinfo=UTC), 100.0, 100.1, 99.4, 99.5),
        ]
        session_end_bar = _bar(datetime(2026, 1, 6, 14, 50, tzinfo=UTC), 100.0, 100.6, 99.9, 100.5)
        breakout_bar = _bar(datetime(2026, 1, 6, 15, 0, tzinfo=UTC), 105.0, 112.5, 108.0, 112.0)
        all_bars = [*warmup_bars, *build_bars, session_end_bar, breakout_bar]

        class FakeConnector:
            def connect(self) -> bool:
                return True

            def disconnect(self) -> None:
                pass

            def fetch_recent_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
                return all_bars

        with (
            patch("live_signal_check.MT5Connector", FakeConnector),
            patch("live_signal_check.send_telegram_alert") as mock_send_alert,
        ):
            live_signal_check.main(["--symbol", "USTEC", "--timeframe", "M5"])

        captured = capsys.readouterr()
        assert "SIGNAL FOUND" in captured.out
        assert "BUY (LONG)" in captured.out
        mock_send_alert.assert_called_once()
        assert mock_send_alert.call_args[0][0].direction == SignalDirection.BUY

    def test_main_still_prints_signal_found_when_telegram_network_fails(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A Telegram network/HTTP failure must never affect the console output
        or crash main() -- it is a best-effort supplementary channel.
        """
        base = datetime(2026, 1, 6, 12, 50, tzinfo=UTC)
        warmup_bars = [
            _bar(base + timedelta(minutes=5 * i), 100.0, 100.5, 99.5, 100.5 if i % 2 == 0 else 99.5)
            for i in range(20)
        ]
        build_bars = [
            _bar(datetime(2026, 1, 6, 14, 30, tzinfo=UTC), 100.0, 100.6, 99.9, 100.5),
            _bar(datetime(2026, 1, 6, 14, 35, tzinfo=UTC), 100.0, 100.1, 99.4, 99.5),
            _bar(datetime(2026, 1, 6, 14, 40, tzinfo=UTC), 100.0, 100.6, 99.9, 100.5),
            _bar(datetime(2026, 1, 6, 14, 45, tzinfo=UTC), 100.0, 100.1, 99.4, 99.5),
        ]
        session_end_bar = _bar(datetime(2026, 1, 6, 14, 50, tzinfo=UTC), 100.0, 100.6, 99.9, 100.5)
        breakout_bar = _bar(datetime(2026, 1, 6, 15, 0, tzinfo=UTC), 105.0, 112.5, 108.0, 112.0)
        all_bars = [*warmup_bars, *build_bars, session_end_bar, breakout_bar]

        class FakeConnector:
            def connect(self) -> bool:
                return True

            def disconnect(self) -> None:
                pass

            def fetch_recent_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
                return all_bars

        with (
            patch("live_signal_check.MT5Connector", FakeConnector),
            # Real Settings.load() picks up whatever is in .env; only the network
            # layer is mocked, to prove the *real* TelegramNotifier/Settings wiring
            # survives a genuine send failure, not just a mocked-away function.
            patch("notifications.telegram.urllib.request.urlopen", side_effect=OSError("network down")),
        ):
            live_signal_check.main(["--symbol", "USTEC", "--timeframe", "M5"])

        captured = capsys.readouterr()
        assert "SIGNAL FOUND" in captured.out
        assert "BUY (LONG)" in captured.out

    def test_main_still_prints_signal_found_when_telegram_notifier_raises_unexpectedly(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Defense in depth: even if TelegramNotifier itself misbehaves (raises
        instead of returning False), send_telegram_alert's own try/except must
        still stop the failure from reaching main()'s caller.
        """
        base = datetime(2026, 1, 6, 12, 50, tzinfo=UTC)
        warmup_bars = [
            _bar(base + timedelta(minutes=5 * i), 100.0, 100.5, 99.5, 100.5 if i % 2 == 0 else 99.5)
            for i in range(20)
        ]
        build_bars = [
            _bar(datetime(2026, 1, 6, 14, 30, tzinfo=UTC), 100.0, 100.6, 99.9, 100.5),
            _bar(datetime(2026, 1, 6, 14, 35, tzinfo=UTC), 100.0, 100.1, 99.4, 99.5),
            _bar(datetime(2026, 1, 6, 14, 40, tzinfo=UTC), 100.0, 100.6, 99.9, 100.5),
            _bar(datetime(2026, 1, 6, 14, 45, tzinfo=UTC), 100.0, 100.1, 99.4, 99.5),
        ]
        session_end_bar = _bar(datetime(2026, 1, 6, 14, 50, tzinfo=UTC), 100.0, 100.6, 99.9, 100.5)
        breakout_bar = _bar(datetime(2026, 1, 6, 15, 0, tzinfo=UTC), 105.0, 112.5, 108.0, 112.0)
        all_bars = [*warmup_bars, *build_bars, session_end_bar, breakout_bar]

        class FakeConnector:
            def connect(self) -> bool:
                return True

            def disconnect(self) -> None:
                pass

            def fetch_recent_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
                return all_bars

        class ExplodingNotifier:
            def __init__(self, bot_token: str, chat_id: str) -> None:
                pass

            def send_message(self, message: str) -> bool:
                raise RuntimeError("simulated unexpected failure")

        with (
            patch("live_signal_check.MT5Connector", FakeConnector),
            patch("live_signal_check.TelegramNotifier", ExplodingNotifier),
        ):
            live_signal_check.main(["--symbol", "USTEC", "--timeframe", "M5"])

        captured = capsys.readouterr()
        assert "SIGNAL FOUND" in captured.out

    def test_main_exits_when_connect_fails(self) -> None:
        class FailingConnector:
            def connect(self) -> bool:
                return False

            def disconnect(self) -> None:
                pass

        with patch("live_signal_check.MT5Connector", FailingConnector):
            with pytest.raises(SystemExit) as exc_info:
                live_signal_check.main(["--symbol", "USTEC"])

        assert exc_info.value.code == 1

    def test_main_disconnects_even_if_evaluation_would_raise(self) -> None:
        """disconnect() must be called via `finally` right after the fetch,
        regardless of what happens afterward (read-only, one-shot session)."""
        disconnect_calls = []

        class FakeConnector:
            def connect(self) -> bool:
                return True

            def disconnect(self) -> None:
                disconnect_calls.append(1)

            def fetch_recent_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
                return [_bar(datetime(2026, 1, 6, 15, 0, tzinfo=UTC), 100.0, 100.5, 99.5, 100.0)]

        with patch("live_signal_check.MT5Connector", FakeConnector):
            live_signal_check.main(["--symbol", "USTEC"])

        assert disconnect_calls == [1]


def _setup(
    symbol: str = "USTEC",
    timeframe: Timeframe = Timeframe.M5,
    direction: SignalDirection = SignalDirection.BUY,
    timestamp: datetime = datetime(2026, 1, 6, 15, 0, tzinfo=UTC),
    setup_id: str = "any_id",
) -> TradeSetup:
    """Minimal TradeSetup fixture for signature tests -- only the fields
    _signal_signature() reads (symbol, timeframe, direction, timestamp) plus
    the required-but-irrelevant-here fields need real values.
    """
    return TradeSetup(
        setup_id=setup_id,
        symbol=symbol,
        timeframe=timeframe,
        direction=direction,
        entry_zone=(100.0, 100.0),
        stop_zone=(90.0, 90.0),
        target_zone=(120.0, 120.0),
        confidence_score=1.0,
        confluence=[],
        trigger_reason="test",
        invalidations=[],
        related_structure_break=None,
        related_order_block=None,
        related_fvg=None,
        timestamp=timestamp,
        strategy_name="NasdaqMidlineSweepStrategy",
    )


class TestSignalSignature:
    """_signal_signature must be stable across independently-generated
    TradeSetup objects for the same underlying bar (even though setup_id
    itself is randomized per call -- see NasdaqMidlineSweepStrategy.evaluate()),
    and must differ whenever any of symbol/timeframe/direction/bar timestamp
    differs.
    """

    def test_same_bar_same_signature_despite_different_setup_id(self) -> None:
        a = _setup(setup_id="random_id_1")
        b = _setup(setup_id="random_id_2")
        assert live_signal_check._signal_signature(a) == live_signal_check._signal_signature(b)

    def test_different_timestamp_different_signature(self) -> None:
        a = _setup(timestamp=datetime(2026, 1, 6, 15, 0, tzinfo=UTC))
        b = _setup(timestamp=datetime(2026, 1, 6, 15, 5, tzinfo=UTC))
        assert live_signal_check._signal_signature(a) != live_signal_check._signal_signature(b)

    def test_different_direction_different_signature(self) -> None:
        a = _setup(direction=SignalDirection.BUY)
        b = _setup(direction=SignalDirection.SELL)
        assert live_signal_check._signal_signature(a) != live_signal_check._signal_signature(b)

    def test_different_symbol_different_signature(self) -> None:
        a = _setup(symbol="USTEC")
        b = _setup(symbol="US30")
        assert live_signal_check._signal_signature(a) != live_signal_check._signal_signature(b)

    def test_different_timeframe_different_signature(self) -> None:
        a = _setup(timeframe=Timeframe.M5)
        b = _setup(timeframe=Timeframe.M15)
        assert live_signal_check._signal_signature(a) != live_signal_check._signal_signature(b)


class TestSignalStatePersistence:
    """_already_sent/_record_sent round-trip, and fail-open on read/write errors."""

    def test_not_sent_when_state_file_missing(self) -> None:
        assert live_signal_check._already_sent("sig_a") is False

    def test_record_then_already_sent_same_signature(self) -> None:
        live_signal_check._record_sent("sig_a")
        assert live_signal_check._already_sent("sig_a") is True

    def test_already_sent_false_for_different_signature(self) -> None:
        live_signal_check._record_sent("sig_a")
        assert live_signal_check._already_sent("sig_b") is False

    def test_record_overwrites_previous_signature(self) -> None:
        live_signal_check._record_sent("sig_a")
        live_signal_check._record_sent("sig_b")
        assert live_signal_check._already_sent("sig_a") is False
        assert live_signal_check._already_sent("sig_b") is True

    def test_fail_open_on_corrupt_state_file(self) -> None:
        """A corrupt/unreadable state file must be treated as 'not sent yet',
        never as 'already sent' -- the latter could silently suppress a real,
        new signal, which is worse than an occasional harmless duplicate.
        """
        live_signal_check.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        live_signal_check.STATE_FILE.write_text("not valid json{{{")
        assert live_signal_check._already_sent("sig_a") is False

    def test_record_sent_does_not_raise_when_parent_cannot_be_created(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_record_sent must never raise -- a broken dedup state must degrade
        the dedup feature only, never crash the caller (main())."""
        blocked_parent = tmp_path / "blocked"
        blocked_parent.write_text("a file, not a directory")  # mkdir() on this path will raise
        monkeypatch.setattr(live_signal_check, "STATE_FILE", blocked_parent / "state.json")

        live_signal_check._record_sent("sig_a")  # must not raise

    def test_already_sent_false_when_state_file_unreadable_dict_missing_key(self) -> None:
        """Valid JSON but without the expected key must be treated as 'not sent'."""
        live_signal_check.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        live_signal_check.STATE_FILE.write_text('{"unexpected_key": "value"}')
        assert live_signal_check._already_sent("sig_a") is False


class TestMainDedupIntegration:
    """End-to-end: two consecutive main() runs seeing the identical final bar
    must send exactly one Telegram alert, not two.
    """

    def _bars_with_breakout(self) -> list[Bar]:
        base = datetime(2026, 1, 6, 12, 50, tzinfo=UTC)
        warmup_bars = [
            _bar(base + timedelta(minutes=5 * i), 100.0, 100.5, 99.5, 100.5 if i % 2 == 0 else 99.5)
            for i in range(20)
        ]
        build_bars = [
            _bar(datetime(2026, 1, 6, 14, 30, tzinfo=UTC), 100.0, 100.6, 99.9, 100.5),
            _bar(datetime(2026, 1, 6, 14, 35, tzinfo=UTC), 100.0, 100.1, 99.4, 99.5),
            _bar(datetime(2026, 1, 6, 14, 40, tzinfo=UTC), 100.0, 100.6, 99.9, 100.5),
            _bar(datetime(2026, 1, 6, 14, 45, tzinfo=UTC), 100.0, 100.1, 99.4, 99.5),
        ]
        session_end_bar = _bar(datetime(2026, 1, 6, 14, 50, tzinfo=UTC), 100.0, 100.6, 99.9, 100.5)
        breakout_bar = _bar(datetime(2026, 1, 6, 15, 0, tzinfo=UTC), 105.0, 112.5, 108.0, 112.0)
        return [*warmup_bars, *build_bars, session_end_bar, breakout_bar]

    def test_second_run_seeing_same_final_bar_suppresses_duplicate_alert(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        all_bars = self._bars_with_breakout()

        class FakeConnector:
            def connect(self) -> bool:
                return True

            def disconnect(self) -> None:
                pass

            def fetch_recent_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
                return all_bars

        with (
            patch("live_signal_check.MT5Connector", FakeConnector),
            patch("live_signal_check.send_telegram_alert") as mock_send_alert,
        ):
            # Two independent "process runs" seeing the identical bar history
            # (e.g. scheduler fired twice before a new bar closed).
            live_signal_check.main(["--symbol", "USTEC", "--timeframe", "M5"])
            live_signal_check.main(["--symbol", "USTEC", "--timeframe", "M5"])

        captured = capsys.readouterr()
        assert captured.out.count("SIGNAL FOUND") == 2  # console output is unaffected by dedup
        mock_send_alert.assert_called_once()  # but Telegram was only notified once

    def test_different_final_bar_still_alerts_on_second_run(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Sanity check: dedup must not suppress a genuinely NEW bar's signal."""
        first_bars = self._bars_with_breakout()
        # Same OHLC shape as the proven breakout_bar in _bars_with_breakout()
        # (sweep+reclaim+displacement), just 5 minutes later -- a genuinely
        # different bar, not a re-evaluation of the same one.
        second_bars = [
            *first_bars[:-1],
            _bar(datetime(2026, 1, 6, 15, 5, tzinfo=UTC), 105.0, 112.5, 108.0, 112.0),
        ]

        class FakeConnector:
            def __init__(self, bars: list[Bar]) -> None:
                self._bars = bars

            def connect(self) -> bool:
                return True

            def disconnect(self) -> None:
                pass

            def fetch_recent_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
                return self._bars

        with patch("live_signal_check.send_telegram_alert") as mock_send_alert:
            with patch("live_signal_check.MT5Connector", lambda: FakeConnector(first_bars)):
                live_signal_check.main(["--symbol", "USTEC", "--timeframe", "M5"])
            with patch("live_signal_check.MT5Connector", lambda: FakeConnector(second_bars)):
                live_signal_check.main(["--symbol", "USTEC", "--timeframe", "M5"])

        assert mock_send_alert.call_count == 2
