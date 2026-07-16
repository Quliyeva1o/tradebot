"""Unit tests for live_signal_check.py (read-only, one-shot Midline Sweep signal check).

No real MT5 connection is made anywhere in this file.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

import live_signal_check
from core.models import Bar, SignalDirection, Timeframe
from strategy.diagnostics import RejectionReason
from strategy.nasdaq_midline_sweep import NasdaqMidlineSweepStrategy


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

        with patch("live_signal_check.MT5Connector", FakeConnector):
            live_signal_check.main(["--symbol", "USTEC", "--timeframe", "M5"])

        captured = capsys.readouterr()
        assert "SIGNAL FOUND" in captured.out
        assert "BUY (LONG)" in captured.out

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
