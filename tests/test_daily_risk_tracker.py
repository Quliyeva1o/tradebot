"""Unit tests for risk/daily_risk_tracker.py.

risk.kill_switch.activate_kill_switch/is_trading_halted are mocked
throughout (imported by name into daily_risk_tracker's own namespace, so
patched as "risk.daily_risk_tracker.activate_kill_switch" per unittest.mock's
patch-where-it's-used rule) -- these tests are about DailyRiskTracker's own
baseline/threshold logic, not kill-switch mechanics (covered separately in
test_kill_switch.py).
"""

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

import risk.daily_risk_tracker as tracker_module
from config.settings import Settings
from risk.daily_risk_tracker import DailyRiskTracker


@pytest.fixture(autouse=True)
def _isolated_state_file(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(tracker_module, "STATE_FILE", tmp_path / "daily_risk_state.json")


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _write_state(date: str, day_start_equity) -> None:
    tracker_module.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tracker_module.STATE_FILE.write_text(json.dumps({"date": date, "day_start_equity": day_start_equity}))


class TestNewBaseline:
    def test_first_call_ever_records_baseline_and_returns_false(self) -> None:
        risk_tracker = DailyRiskTracker(max_daily_loss_pct=0.05)
        with patch.object(tracker_module, "activate_kill_switch") as mock_activate:
            result = risk_tracker.check_and_update(10_000.0)

        assert result is False
        mock_activate.assert_not_called()
        state = json.loads(tracker_module.STATE_FILE.read_text())
        assert state == {"date": _today(), "day_start_equity": 10_000.0}

    def test_new_calendar_day_resets_baseline_to_current_equity(self) -> None:
        _write_state("2000-01-01", 5_000.0)  # unambiguously a different day
        risk_tracker = DailyRiskTracker(max_daily_loss_pct=0.05)

        with patch.object(tracker_module, "activate_kill_switch") as mock_activate:
            result = risk_tracker.check_and_update(9_000.0)

        assert result is False
        mock_activate.assert_not_called()  # nothing to compare against yet on a new day
        state = json.loads(tracker_module.STATE_FILE.read_text())
        assert state == {"date": _today(), "day_start_equity": 9_000.0}


class TestThresholdCheck:
    def test_small_loss_under_threshold_does_not_trigger(self) -> None:
        _write_state(_today(), 10_000.0)
        risk_tracker = DailyRiskTracker(max_daily_loss_pct=0.05)

        with patch.object(tracker_module, "activate_kill_switch") as mock_activate:
            result = risk_tracker.check_and_update(9_700.0)  # 3% loss

        assert result is False
        mock_activate.assert_not_called()

    def test_loss_over_threshold_triggers(self) -> None:
        _write_state(_today(), 10_000.0)
        risk_tracker = DailyRiskTracker(max_daily_loss_pct=0.05)

        with (
            patch.object(tracker_module, "activate_kill_switch") as mock_activate,
            patch.object(tracker_module, "is_trading_halted", return_value=False),
        ):
            result = risk_tracker.check_and_update(9_400.0)  # 6% loss

        assert result is True
        mock_activate.assert_called_once()
        reason = mock_activate.call_args[0][0]
        assert "6.00%" in reason
        assert "5.00%" in reason

    def test_loss_exactly_at_threshold_triggers(self) -> None:
        _write_state(_today(), 10_000.0)
        risk_tracker = DailyRiskTracker(max_daily_loss_pct=0.05)

        with (
            patch.object(tracker_module, "activate_kill_switch") as mock_activate,
            patch.object(tracker_module, "is_trading_halted", return_value=False),
        ):
            result = risk_tracker.check_and_update(9_500.0)  # exactly 5% loss

        assert result is True
        mock_activate.assert_called_once()

    def test_equity_gain_never_triggers(self) -> None:
        _write_state(_today(), 10_000.0)
        risk_tracker = DailyRiskTracker(max_daily_loss_pct=0.05)

        with patch.object(tracker_module, "activate_kill_switch") as mock_activate:
            result = risk_tracker.check_and_update(11_000.0)

        assert result is False
        mock_activate.assert_not_called()

    def test_returns_false_when_already_halted_before_this_call(self) -> None:
        """The kill-switch fires again (idempotently, a no-op inside
        activate_kill_switch itself) but check_and_update's return value
        reflects "did THIS call newly activate it" -- False if it was
        already active."""
        _write_state(_today(), 10_000.0)
        risk_tracker = DailyRiskTracker(max_daily_loss_pct=0.05)

        with (
            patch.object(tracker_module, "activate_kill_switch") as mock_activate,
            patch.object(tracker_module, "is_trading_halted", return_value=True),
        ):
            result = risk_tracker.check_and_update(9_400.0)

        assert result is False
        mock_activate.assert_called_once()  # still called -- idempotency is activate_kill_switch's job


class TestFailOpen:
    def test_corrupt_state_file_resets_baseline_and_does_not_raise(self) -> None:
        tracker_module.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tracker_module.STATE_FILE.write_text("not valid json{{{")
        risk_tracker = DailyRiskTracker(max_daily_loss_pct=0.05)

        with patch.object(tracker_module, "activate_kill_switch") as mock_activate:
            result = risk_tracker.check_and_update(10_000.0)  # must not raise

        assert result is False
        mock_activate.assert_not_called()
        state = json.loads(tracker_module.STATE_FILE.read_text())
        assert state == {"date": _today(), "day_start_equity": 10_000.0}

    def test_missing_state_file_is_treated_as_new_baseline(self) -> None:
        assert not tracker_module.STATE_FILE.exists()
        risk_tracker = DailyRiskTracker(max_daily_loss_pct=0.05)

        result = risk_tracker.check_and_update(10_000.0)

        assert result is False

    @pytest.mark.parametrize("bad_value", [0, -100.0, "not_a_number", None])
    def test_invalid_day_start_equity_resets_baseline(self, bad_value) -> None:
        _write_state(_today(), bad_value)
        risk_tracker = DailyRiskTracker(max_daily_loss_pct=0.05)

        with patch.object(tracker_module, "activate_kill_switch") as mock_activate:
            result = risk_tracker.check_and_update(10_000.0)

        assert result is False
        mock_activate.assert_not_called()
        state = json.loads(tracker_module.STATE_FILE.read_text())
        assert state == {"date": _today(), "day_start_equity": 10_000.0}

    def test_write_failure_does_not_raise(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        blocked_parent = tmp_path / "blocked"
        blocked_parent.write_text("a file, not a directory")  # mkdir() on this path will raise
        monkeypatch.setattr(tracker_module, "STATE_FILE", blocked_parent / "state.json")
        risk_tracker = DailyRiskTracker(max_daily_loss_pct=0.05)

        risk_tracker.check_and_update(10_000.0)  # must not raise


class TestMaxDailyLossPctDefault:
    def test_defaults_to_settings_value_when_not_given(self) -> None:
        risk_tracker = DailyRiskTracker()
        assert risk_tracker.max_daily_loss_pct == Settings.load().MAX_DAILY_LOSS_PCT

    def test_explicit_value_overrides_settings_default(self) -> None:
        risk_tracker = DailyRiskTracker(max_daily_loss_pct=0.10)
        assert risk_tracker.max_daily_loss_pct == 0.10
