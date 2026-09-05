"""Wiring test for run_live_demo_with_crash_alert.py.

The actual crash-detection/logging/alerting logic is shared across every
run_live_*.py's *_with_crash_alert.py wrapper -- see
notifications/crash_alert.py and its own tests/test_crash_alert.py. This
file only checks that THIS wrapper points at the right target script and
crash log.
"""

import run_live_demo_with_crash_alert as supervisor


def test_target_script_is_run_live_demo() -> None:
    assert supervisor.TARGET_SCRIPT.name == "run_live_demo.py"
    assert supervisor.TARGET_SCRIPT.parent == supervisor.ROOT_DIR


def test_crash_log_is_dedicated_to_this_script() -> None:
    assert supervisor.CRASH_LOG.name == "run_live_demo_crashes.log"
