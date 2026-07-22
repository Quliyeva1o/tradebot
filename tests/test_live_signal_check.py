"""Unit tests for live_signal_check.py (read-only, one-shot Midline Sweep signal check).

No real MT5 connection or Telegram send is made anywhere in this file --
tests that reach the signal-found path always mock the Telegram layer too
(main() calls send_telegram_alert() unconditionally on a found signal, and
config.settings.Settings.load() reads whatever is actually configured in the
real .env, so leaving it unmocked here would attempt a real network call).
The same applies to the T3 (Sprint 6c) data-quality alert path -- see
_mock_data_quality_alert below.
"""

import logging
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

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
def _isolated_data_quality_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Points DATA_QUALITY_STATE_FILE at a per-test tmp_path location.

    Same rationale as _isolated_state_file above (Sprint 7's data-quality
    alert dedup, mirroring the trade-signal dedup it's isolating): without
    this, every test that reaches check_data_quality_and_alert() would
    read/write the real repo-relative logs/last_data_quality_alerts.json,
    leaking alerted-signature state across tests that reuse identical bar
    timestamps.
    """
    monkeypatch.setattr(
        live_signal_check, "DATA_QUALITY_STATE_FILE", tmp_path / "last_data_quality_alerts.json"
    )


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


@pytest.fixture(autouse=True)
def _mock_data_quality_alert() -> Generator[MagicMock, None, None]:
    """Prevents every test in this file from sending a real Telegram data-quality alert.

    T3's staleness check (core.data_quality.check_stale) compares each
    fetched bar's timestamp against the real wall clock -- every test bar
    below uses a fixed historical date (e.g. 2026-01-06), which is always
    "stale" relative to whenever the suite actually runs, so
    check_data_quality_and_alert() fires on essentially every test that
    reaches main()'s post-fetch flow. Mirrors _no_real_log_file's rationale.
    Tests that specifically exercise this path (TestDataQualityIntegration)
    request this fixture directly to inspect the mock instead of needing
    their own separate patch.
    """
    with patch("live_signal_check.send_data_quality_alert") as mock_alert:
        yield mock_alert


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
        # Sprint 8: a setup found on this replay bar is discarded, exactly as
        # before -- but no longer marks trade_taken (see
        # NasdaqMidlineSweepStrategy.evaluate()'s record_trade_taken
        # docstring), so final_neutral_bar's own rejection below now reflects
        # its OWN genuine, honest evaluation (NO_DISPLACEMENT: a 0.5 body vs.
        # the ~1.4 average pulled up by breakout_bar's own large body in the
        # SMA window) rather than the previously-misleading
        # TRADE_ALREADY_TAKEN (no trade was ever actually taken).
        final_neutral_bar = _bar(datetime(2026, 1, 5, 10, 5, tzinfo=UTC), 112.0, 113.0, 108.5, 112.5)
        bars = [*_build_session_bars(), _session_end_bar(), breakout_bar, final_neutral_bar]

        setup, diagnostics, final_bar = live_signal_check.check_signal(
            bars, "USTEC", Timeframe.M5, strategy
        )

        assert setup is None  # the breakout happened on an earlier bar, not the final one
        assert final_bar.timestamp == final_neutral_bar.timestamp
        summary = diagnostics["0_NasdaqMidlineSweepStrategy"]
        assert summary["rejections"] == {RejectionReason.NO_DISPLACEMENT.value: 1}

    def test_a_signal_seen_only_during_replay_does_not_block_a_later_genuine_signal(self) -> None:
        """Sprint 8 regression: the actual production bug scenario.

        A missed scheduler cycle means a valid signal bar is only ever seen
        as a REPLAY bar (never as the live "final bar") on the run that
        would have reported it. A completely separate check_signal() call
        later that day (a subsequent run) must still be able to recognize
        and report a genuinely new, independently-qualifying signal -- it
        must not be silently blocked as "trade already taken" by the
        earlier, never-acted-upon replay-only detection.
        """
        strategy = NasdaqMidlineSweepStrategy(sma_period=5, session_timezone="UTC")
        missed_breakout = _bar(datetime(2026, 1, 5, 10, 0, tzinfo=UTC), 105.0, 112.5, 108.0, 112.0)
        later_breakout = _bar(datetime(2026, 1, 5, 10, 5, tzinfo=UTC), 105.0, 112.5, 108.0, 112.0)

        # Run 1 (simulated): the scheduler cycle that should have caught
        # missed_breakout as the live final bar never happened -- it is only
        # ever replayed as a non-final bar, in the SAME check_signal() call
        # that later reports later_breakout as final. This directly mirrors
        # what a real missed run + the next real run would produce.
        bars = [*_build_session_bars(), _session_end_bar(), missed_breakout, later_breakout]

        setup, _diagnostics, final_bar = live_signal_check.check_signal(
            bars, "USTEC", Timeframe.M5, strategy
        )

        assert setup is not None  # the later, independent signal correctly still fires
        assert setup.direction == SignalDirection.BUY
        assert final_bar.timestamp == later_breakout.timestamp


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


class TestKillSwitchIntegration:
    """Phase 6 kill-switch infrastructure (risk/kill_switch.py): main() must
    check is_trading_halted() before doing anything else -- no MT5 connection,
    no bar fetch, no Telegram alert -- when trading is halted.
    """

    def test_halted_never_connects_to_mt5(self, capsys: pytest.CaptureFixture[str]) -> None:
        connect_calls = []

        class FakeConnector:
            def connect(self) -> bool:
                connect_calls.append(1)
                return True

            def disconnect(self) -> None:
                pass

            def fetch_recent_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
                raise AssertionError("fetch_recent_bars must never be called while halted")

        with (
            patch("live_signal_check.is_trading_halted", return_value=True),
            patch("live_signal_check.MT5Connector", FakeConnector),
        ):
            live_signal_check.main(["--symbol", "USTEC", "--timeframe", "M5"])

        assert connect_calls == []

    def test_halted_prints_status_and_does_not_raise(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("live_signal_check.is_trading_halted", return_value=True):
            live_signal_check.main(["--symbol", "USTEC", "--timeframe", "M5"])

        captured = capsys.readouterr()
        assert "TRADING HALTED" in captured.out

    def test_halted_never_sends_telegram_alert(self) -> None:
        with (
            patch("live_signal_check.is_trading_halted", return_value=True),
            patch("live_signal_check.send_telegram_alert") as mock_send_alert,
        ):
            live_signal_check.main(["--symbol", "USTEC", "--timeframe", "M5"])

        mock_send_alert.assert_not_called()

    def test_not_halted_proceeds_normally(self) -> None:
        """Sanity check: is_trading_halted()=False must not change existing behavior."""
        disconnect_calls = []

        class FakeConnector:
            def connect(self) -> bool:
                return True

            def disconnect(self) -> None:
                disconnect_calls.append(1)

            def fetch_recent_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
                return [_bar(datetime(2026, 1, 6, 15, 0, tzinfo=UTC), 100.0, 100.5, 99.5, 100.0)]

        with (
            patch("live_signal_check.is_trading_halted", return_value=False),
            patch("live_signal_check.MT5Connector", FakeConnector),
        ):
            live_signal_check.main(["--symbol", "USTEC"])

        assert disconnect_calls == [1]


class TestDataQualityIntegration:
    """Tests for T3 (Sprint 6c): main() wires in check_data_quality_and_alert().

    Purely additive to the fetch step -- check_signal()'s own detection
    logic and result are unaffected either way. These tests use minimal
    bars that never form a signal, since the point is proving the
    data-quality hook fires/doesn't fire correctly, not exercising a full
    strategy scenario (already covered elsewhere in this file).
    """

    def test_stale_bars_trigger_the_alert(
        self, _mock_data_quality_alert: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Fixed historical date -- always "stale" relative to real wall-clock now().
        bars = [_bar(datetime(2026, 1, 6, 15, 0, tzinfo=UTC), 100.0, 100.5, 99.5, 100.0)]

        class FakeConnector:
            def connect(self) -> bool:
                return True

            def disconnect(self) -> None:
                pass

            def fetch_recent_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
                return bars

        with patch("live_signal_check.MT5Connector", FakeConnector):
            live_signal_check.main(["--symbol", "USTEC", "--timeframe", "M5"])

        _mock_data_quality_alert.assert_called_once()
        issues = _mock_data_quality_alert.call_args[0][0]
        assert any(issue.kind == "stale" for issue in issues)
        # check_signal()'s own result is unaffected -- still runs and reports normally.
        captured = capsys.readouterr()
        assert "NO SIGNAL" in captured.out

    def test_fresh_gap_free_bars_do_not_trigger_the_alert(
        self, _mock_data_quality_alert: MagicMock
    ) -> None:
        # Dynamically anchored to real "now" so the staleness check finds it fresh.
        now = datetime.now(UTC)
        bars = [
            _bar(now - timedelta(minutes=5 * i), 100.0, 100.5, 99.5, 100.0) for i in range(5, 0, -1)
        ]

        class FakeConnector:
            def connect(self) -> bool:
                return True

            def disconnect(self) -> None:
                pass

            def fetch_recent_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
                return bars

        with patch("live_signal_check.MT5Connector", FakeConnector):
            live_signal_check.main(["--symbol", "USTEC", "--timeframe", "M5"])

        _mock_data_quality_alert.assert_not_called()

    def test_gap_between_fetched_bars_triggers_the_alert(
        self, _mock_data_quality_alert: MagicMock
    ) -> None:
        now = datetime.now(UTC)
        bars = [
            _bar(now - timedelta(minutes=60), 100.0, 100.5, 99.5, 100.0),  # big intraday gap
            _bar(now - timedelta(minutes=5), 100.0, 100.5, 99.5, 100.0),
        ]

        class FakeConnector:
            def connect(self) -> bool:
                return True

            def disconnect(self) -> None:
                pass

            def fetch_recent_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
                return bars

        with patch("live_signal_check.MT5Connector", FakeConnector):
            live_signal_check.main(["--symbol", "USTEC", "--timeframe", "M5"])

        _mock_data_quality_alert.assert_called_once()
        issues = _mock_data_quality_alert.call_args[0][0]
        assert any(issue.kind == "gap" for issue in issues)

    def test_data_quality_issue_does_not_prevent_signal_evaluation(
        self, _mock_data_quality_alert: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A data-quality issue is reported, not a reason to abort.

        check_signal() still runs against whatever bars were actually
        fetched (same fixture as TestMainEndToEnd's signal-found scenario,
        just with an old fetch timestamp that also trips the staleness check).
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

        _mock_data_quality_alert.assert_called_once()  # stale, per the fixed historical date
        captured = capsys.readouterr()
        assert "SIGNAL FOUND" in captured.out  # check_signal() still ran normally
        mock_send_alert.assert_called_once()


class TestDataQualityAlertDedup:
    """Sprint 7: Telegram dedup for gap/stale-bar alerts.

    Mirrors the existing trade-signal dedup mechanism's exact design
    (_signal_signature()/_already_sent()/_record_sent(), tested in
    TestSignalSignature/TestSignalStatePersistence/TestMainDedupIntegration
    above) -- here calling check_data_quality_and_alert() directly (not
    through main()) since that is the actual unit under test and doing so
    keeps these tests independent of check_signal()'s own detection.
    """

    def _gap_bar(self, ts: datetime) -> Bar:
        return _bar(ts, 100.0, 100.5, 99.5, 100.0)

    def test_same_gap_alerts_once_then_is_suppressed_on_later_runs(self) -> None:
        bars = [
            self._gap_bar(datetime(2026, 1, 5, 9, 0, tzinfo=UTC)),
            self._gap_bar(datetime(2026, 1, 5, 9, 30, tzinfo=UTC)),  # 30 min gap (> 7.5 min threshold)
        ]

        with patch("live_signal_check.send_data_quality_alert") as mock_alert:
            live_signal_check.check_data_quality_and_alert(bars, "USTEC", Timeframe.M5, "M5")
            live_signal_check.check_data_quality_and_alert(bars, "USTEC", Timeframe.M5, "M5")
            live_signal_check.check_data_quality_and_alert(bars, "USTEC", Timeframe.M5, "M5")

        mock_alert.assert_called_once()

    def test_a_genuinely_new_gap_still_alerts_while_an_old_one_is_suppressed(self) -> None:
        bar_900 = self._gap_bar(datetime(2026, 1, 5, 9, 0, tzinfo=UTC))
        bar_930 = self._gap_bar(datetime(2026, 1, 5, 9, 30, tzinfo=UTC))  # gap 1: 9:00 -> 9:30 (30 min)
        bar_935 = self._gap_bar(datetime(2026, 1, 5, 9, 35, tzinfo=UTC))  # 9:30 -> 9:35 (5 min, no gap)
        bar_1015 = self._gap_bar(datetime(2026, 1, 5, 10, 15, tzinfo=UTC))  # gap 2: 9:35 -> 10:15 (40 min)

        with patch("live_signal_check.send_data_quality_alert") as mock_alert:
            # Run 1: only gap 1 exists yet (e.g. gap 2's second bar hasn't formed).
            live_signal_check.check_data_quality_and_alert(
                [bar_900, bar_930], "USTEC", Timeframe.M5, "M5"
            )
            # Run 2: gap 1 is still in the lookback window (same signature) AND
            # a genuinely new gap 2 has now appeared.
            live_signal_check.check_data_quality_and_alert(
                [bar_900, bar_930, bar_935, bar_1015], "USTEC", Timeframe.M5, "M5"
            )

        assert mock_alert.call_count == 2
        run_2_issues = mock_alert.call_args_list[1][0][0]
        # These fixed-historical-date bars also always trip check_stale() (see
        # _mock_data_quality_alert's docstring above) -- its own event_key is
        # the latest bar's timestamp, which changed between run 1 and run 2,
        # so it is independently "new" both times and is present in run 2's
        # batch alongside the new gap. Only gap 1 (unchanged latest bar pair)
        # is actually suppressed here.
        run_2_gap_issues = [issue for issue in run_2_issues if issue.kind == "gap"]
        assert len(run_2_gap_issues) == 1  # gap 1 suppressed; only the new gap 2 is sent
        assert "09:35:00" in run_2_gap_issues[0].message

    def test_local_logging_still_happens_every_run_even_when_telegram_is_suppressed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        bars = [
            self._gap_bar(datetime(2026, 1, 5, 9, 0, tzinfo=UTC)),
            self._gap_bar(datetime(2026, 1, 5, 9, 30, tzinfo=UTC)),
        ]

        with patch("live_signal_check.send_data_quality_alert") as mock_alert:
            with caplog.at_level(logging.INFO):
                live_signal_check.check_data_quality_and_alert(bars, "USTEC", Timeframe.M5, "M5")
                caplog.clear()
                # Second run: Telegram send is now suppressed (duplicate signature)...
                live_signal_check.check_data_quality_and_alert(bars, "USTEC", Timeframe.M5, "M5")

        mock_alert.assert_called_once()  # only sent on run 1
        # ...but the human-readable WARNING and structured JSON log lines
        # still fire on run 2, unconditionally, every time.
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("DATA QUALITY: [gap]" in r.message for r in warning_records)
        structured_records = [r for r in caplog.records if r.name == "data_quality_events"]
        assert any(
            isinstance(r.msg, dict) and r.msg.get("kind") == "gap" for r in structured_records
        )

    def test_signatures_are_scoped_per_symbol_and_timeframe(self) -> None:
        """The same gap shape on a different symbol/timeframe is a different signature."""
        bars = [
            self._gap_bar(datetime(2026, 1, 5, 9, 0, tzinfo=UTC)),
            self._gap_bar(datetime(2026, 1, 5, 9, 30, tzinfo=UTC)),
        ]

        with patch("live_signal_check.send_data_quality_alert") as mock_alert:
            live_signal_check.check_data_quality_and_alert(bars, "USTEC", Timeframe.M5, "M5")
            live_signal_check.check_data_quality_and_alert(bars, "EURUSD", Timeframe.M5, "M5")

        assert mock_alert.call_count == 2

    def test_dedup_state_self_prunes_once_an_issue_is_no_longer_detected(self) -> None:
        """A gap rolling out of the lookback window drops its signature.

        Once no longer detected, an identical gap shape appearing again
        later (extremely unlikely in practice, since exact timestamps are
        part of the signature, but tested here for the mechanism itself) is
        treated as new, not suppressed.
        """
        bars = [
            self._gap_bar(datetime(2026, 1, 5, 9, 0, tzinfo=UTC)),
            self._gap_bar(datetime(2026, 1, 5, 9, 30, tzinfo=UTC)),
        ]
        # Dynamically anchored to real "now" (unlike `bars` above) so
        # check_stale() also finds this run's data fresh -- genuinely zero
        # issues detected this run, not just zero NEW ones.
        now = datetime.now(UTC)
        fresh_bars = [_bar(now - timedelta(minutes=5 * i), 100.0, 100.5, 99.5, 100.0) for i in range(3, 0, -1)]

        with patch("live_signal_check.send_data_quality_alert") as mock_alert:
            live_signal_check.check_data_quality_and_alert(bars, "USTEC", Timeframe.M5, "M5")
            live_signal_check.check_data_quality_and_alert(fresh_bars, "USTEC", Timeframe.M5, "M5")
            live_signal_check.check_data_quality_and_alert(bars, "USTEC", Timeframe.M5, "M5")

        assert mock_alert.call_count == 2  # run 1 and run 3 both alert; run 2 had nothing to alert
