"""Unit tests for notifications/crash_alert.py (shared crash-visibility
supervisor logic used by every run_live_*.py's *_with_crash_alert.py wrapper).

The wrapped target script is never actually invoked here -- subprocess.run is
always mocked, matching this file's own no-real-subprocess, no-real-network
testing convention (mirrors tests/test_telegram.py: no real HTTP request is
made either).
"""

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import notifications.crash_alert as crash_alert

FAKE_TOKEN = "123456789:AAFakeSecretTokenValueForTestingOnly-XYZ"
FAKE_CHAT_ID = "-1001234567890"
TARGET_SCRIPT = Path("/fake/root/run_live_demo.py")


@pytest.fixture
def crash_log(tmp_path: Path) -> Path:
    return tmp_path / "run_live_demo_crashes.log"


@pytest.fixture(autouse=True)
def _fake_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolates Settings.load() from the real .env for every test.

    Fixed to known fake credentials here so tests never depend on (or
    accidentally exercise) real configured values.
    """
    fake_settings = Mock(TELEGRAM_TOKEN=FAKE_TOKEN, TELEGRAM_CHAT_ID=FAKE_CHAT_ID)
    monkeypatch.setattr(crash_alert.Settings, "load", classmethod(lambda cls: fake_settings))


def _completed_process(returncode: int, stdout: str = "", stderr: str = "") -> Mock:
    return Mock(returncode=returncode, stdout=stdout, stderr=stderr)


class TestNonZeroExitCrashesAndAlerts:
    """A non-zero exit code from the wrapped subprocess triggers the alert path."""

    def test_non_zero_exit_writes_a_crash_log_entry(self, crash_log: Path) -> None:
        with patch(
            "notifications.crash_alert.subprocess.run",
            return_value=_completed_process(1, stdout="fetching bars...", stderr="ImportError: numpy"),
        ):
            with patch("notifications.crash_alert.TelegramNotifier"):
                crash_alert.run_and_alert_on_crash(TARGET_SCRIPT, crash_log, ["--symbol", "USTEC"])

        assert crash_log.exists()
        content = crash_log.read_text(encoding="utf-8")
        assert "run_live_demo.py CRASHED (exit code 1)" in content
        assert "ImportError: numpy" in content
        assert "fetching bars..." in content

    def test_non_zero_exit_sends_a_telegram_alert(self, crash_log: Path) -> None:
        with patch(
            "notifications.crash_alert.subprocess.run",
            return_value=_completed_process(1, stderr="ImportError: numpy"),
        ):
            with patch("notifications.crash_alert.TelegramNotifier") as mock_notifier_cls:
                crash_alert.run_and_alert_on_crash(TARGET_SCRIPT, crash_log, [])

        mock_notifier_cls.assert_called_once_with(FAKE_TOKEN, FAKE_CHAT_ID)
        mock_notifier_cls.return_value.send_message.assert_called_once()
        message = mock_notifier_cls.return_value.send_message.call_args[0][0]
        assert "run_live_demo.py CRASHED" in message
        assert str(crash_log) in message

    def test_telegram_alert_does_not_dump_the_full_traceback(self, crash_log: Path) -> None:
        """Short message only -- the full output belongs in the crash log, not Telegram."""
        long_traceback = "Traceback (most recent call last):\n" + ("  File ...\n" * 50)
        with patch(
            "notifications.crash_alert.subprocess.run",
            return_value=_completed_process(1, stderr=long_traceback),
        ):
            with patch("notifications.crash_alert.TelegramNotifier") as mock_notifier_cls:
                crash_alert.run_and_alert_on_crash(TARGET_SCRIPT, crash_log, [])

        message = mock_notifier_cls.return_value.send_message.call_args[0][0]
        assert long_traceback not in message
        # Much shorter than the 50-line traceback (well over 700 chars) --
        # not a tight fixed bound, since the message legitimately embeds
        # crash_log's own path length (a deep pytest tmp_path here, a real
        # deployment path in production).
        assert len(message) < len(long_traceback) / 2

    def test_returns_the_childs_own_exit_code(self, crash_log: Path) -> None:
        with patch(
            "notifications.crash_alert.subprocess.run",
            return_value=_completed_process(1, stderr="boom"),
        ):
            with patch("notifications.crash_alert.TelegramNotifier"):
                exit_code = crash_alert.run_and_alert_on_crash(TARGET_SCRIPT, crash_log, [])

        assert exit_code == 1

    def test_traceback_in_stderr_with_zero_exit_code_still_counts_as_a_crash(self, crash_log: Path) -> None:
        """Belt-and-suspenders fallback: a printed-but-not-propagated traceback."""
        with patch(
            "notifications.crash_alert.subprocess.run",
            return_value=_completed_process(
                0, stderr="Traceback (most recent call last):\n  ...\nValueError: bad state"
            ),
        ):
            with patch("notifications.crash_alert.TelegramNotifier") as mock_notifier_cls:
                crash_alert.run_and_alert_on_crash(TARGET_SCRIPT, crash_log, [])

        assert crash_log.exists()
        mock_notifier_cls.return_value.send_message.assert_called_once()


class TestSuccessfulRunIsAPassthrough:
    """Exit code 0 must be silent -- no extra logging, no Telegram noise."""

    def test_exit_code_zero_does_not_write_a_crash_log_entry(self, crash_log: Path) -> None:
        with patch(
            "notifications.crash_alert.subprocess.run",
            return_value=_completed_process(0, stdout="RESULT: NO SIGNAL"),
        ):
            with patch("notifications.crash_alert.TelegramNotifier") as mock_notifier_cls:
                crash_alert.run_and_alert_on_crash(TARGET_SCRIPT, crash_log, [])

        assert not crash_log.exists()
        mock_notifier_cls.assert_not_called()

    def test_exit_code_zero_returns_zero(self, crash_log: Path) -> None:
        with patch(
            "notifications.crash_alert.subprocess.run", return_value=_completed_process(0)
        ):
            exit_code = crash_alert.run_and_alert_on_crash(TARGET_SCRIPT, crash_log, [])

        assert exit_code == 0

    def test_forwards_argv_to_the_child_command(self, crash_log: Path) -> None:
        with patch(
            "notifications.crash_alert.subprocess.run", return_value=_completed_process(0)
        ) as mock_run:
            crash_alert.run_and_alert_on_crash(
                TARGET_SCRIPT, crash_log, ["--symbol", "USTEC", "--timeframe", "M5"]
            )

        command = mock_run.call_args[0][0]
        assert command[-4:] == ["--symbol", "USTEC", "--timeframe", "M5"]
        assert str(TARGET_SCRIPT) in command


class TestCrashAlertingIsBestEffort:
    """Neither alert channel's own failure may crash the wrapper or block the other."""

    def test_telegram_failure_does_not_prevent_the_crash_log_from_being_written(self, crash_log: Path) -> None:
        with patch(
            "notifications.crash_alert.subprocess.run",
            return_value=_completed_process(1, stderr="boom"),
        ):
            with patch(
                "notifications.crash_alert.TelegramNotifier",
                side_effect=RuntimeError("network unreachable"),
            ):
                exit_code = crash_alert.run_and_alert_on_crash(TARGET_SCRIPT, crash_log, [])  # must not raise

        assert exit_code == 1
        assert crash_log.exists()

    def test_crash_log_write_failure_does_not_prevent_the_telegram_alert(
        self, tmp_path: Path
    ) -> None:
        # Point crash_log at a path whose parent cannot be created (a file,
        # not a directory, blocks mkdir()).
        blocked = tmp_path / "blocked_file"
        blocked.write_text("not a directory")
        bad_crash_log = blocked / "logs" / "run_live_demo_crashes.log"

        with patch(
            "notifications.crash_alert.subprocess.run",
            return_value=_completed_process(1, stderr="boom"),
        ):
            with patch("notifications.crash_alert.TelegramNotifier") as mock_notifier_cls:
                exit_code = crash_alert.run_and_alert_on_crash(TARGET_SCRIPT, bad_crash_log, [])  # must not raise

        assert exit_code == 1
        mock_notifier_cls.return_value.send_message.assert_called_once()
