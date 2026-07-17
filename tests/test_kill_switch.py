"""Unit tests for risk/kill_switch.py.

No real Telegram send is made anywhere in this file -- Settings.load()
reads whatever is actually configured in the real .env, so leaving the
network layer unmocked would attempt a real HTTP call.
"""

from unittest.mock import patch

import pytest

import risk.kill_switch as kill_switch


@pytest.fixture(autouse=True)
def _isolated_flag(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Points KILL_SWITCH_FLAG at a per-test tmp_path location."""
    monkeypatch.setattr(kill_switch, "KILL_SWITCH_FLAG", tmp_path / "kill_switch.flag")


class TestIsTradingHalted:
    def test_false_when_flag_missing(self) -> None:
        assert kill_switch.is_trading_halted() is False

    def test_true_when_flag_exists(self) -> None:
        kill_switch.KILL_SWITCH_FLAG.write_text("manually created")
        assert kill_switch.is_trading_halted() is True


class TestActivateKillSwitch:
    def test_creates_flag_file(self) -> None:
        with patch("risk.kill_switch.TelegramNotifier"):
            kill_switch.activate_kill_switch("daily loss limit breached")

        assert kill_switch.is_trading_halted() is True

    def test_flag_file_contains_reason(self) -> None:
        with patch("risk.kill_switch.TelegramNotifier"):
            kill_switch.activate_kill_switch("daily loss limit breached")

        assert "daily loss limit breached" in kill_switch.KILL_SWITCH_FLAG.read_text()

    def test_sends_telegram_alert(self) -> None:
        with patch("risk.kill_switch.TelegramNotifier") as mock_notifier_cls:
            kill_switch.activate_kill_switch("daily loss limit breached")

        mock_notifier_cls.return_value.send_message.assert_called_once()
        message = mock_notifier_cls.return_value.send_message.call_args[0][0]
        assert "RISK LIMIT HIT" in message
        assert "daily loss limit breached" in message

    def test_idempotent_second_call_does_not_resend_alert(self) -> None:
        """Once active, repeated calls (e.g. every cycle from a future
        order-engine loop) must not re-send the Telegram alert."""
        with patch("risk.kill_switch.TelegramNotifier") as mock_notifier_cls:
            kill_switch.activate_kill_switch("first reason")
            kill_switch.activate_kill_switch("second reason (should be ignored)")

        mock_notifier_cls.return_value.send_message.assert_called_once()
        assert "first reason" in kill_switch.KILL_SWITCH_FLAG.read_text()
        assert "second reason" not in kill_switch.KILL_SWITCH_FLAG.read_text()

    def test_telegram_failure_does_not_raise(self) -> None:
        """A Telegram network/HTTP failure must never prevent the flag from
        having been created (the actual halt), nor propagate to the caller."""
        with patch(
            "risk.kill_switch.TelegramNotifier",
            side_effect=RuntimeError("simulated failure"),
        ):
            kill_switch.activate_kill_switch("daily loss limit breached")  # must not raise

        assert kill_switch.is_trading_halted() is True

    def test_no_deactivate_function_exists(self) -> None:
        """Deliberate design invariant: only a human, on the filesystem, may
        clear the kill-switch -- there must be no programmatic clear/reset."""
        assert not hasattr(kill_switch, "deactivate_kill_switch")
        assert not hasattr(kill_switch, "clear_kill_switch")
        assert not hasattr(kill_switch, "reset_kill_switch")
