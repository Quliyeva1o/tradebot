"""Wiring tests for every run_live_*_with_crash_alert.py wrapper script.

The actual crash-detection/logging/alerting logic is shared across all of
them -- see notifications/crash_alert.py and its own tests/test_crash_alert.py.
This file only checks that each wrapper points at its own correct target
script and a dedicated (non-colliding) crash log. run_live_demo's own
wrapper has its dedicated tests/test_run_live_demo_with_crash_alert.py.
"""

import importlib

import pytest

WRAPPERS = [
    ("run_live_xauusd_orb_with_crash_alert", "run_live_xauusd_orb.py", "run_live_xauusd_orb_crashes.log"),
    ("run_live_nasdaq_orb_with_crash_alert", "run_live_nasdaq_orb.py", "run_live_nasdaq_orb_crashes.log"),
    ("run_live_sr_bias_with_crash_alert", "run_live_sr_bias.py", "run_live_sr_bias_crashes.log"),
    (
        "run_live_accumulation_breakout_with_crash_alert",
        "run_live_accumulation_breakout.py",
        "run_live_accumulation_breakout_crashes.log",
    ),
    ("run_live_midnight_fvg_with_crash_alert", "run_live_midnight_fvg.py", "run_live_midnight_fvg_crashes.log"),
    ("run_live_first_fvg_15m_with_crash_alert", "run_live_first_fvg_15m.py", "run_live_first_fvg_15m_crashes.log"),
]


@pytest.mark.parametrize("module_name,expected_target,expected_crash_log", WRAPPERS)
def test_wrapper_targets_its_own_script_and_a_dedicated_crash_log(
    module_name: str, expected_target: str, expected_crash_log: str
) -> None:
    module = importlib.import_module(module_name)
    assert module.TARGET_SCRIPT.name == expected_target
    assert module.TARGET_SCRIPT.parent == module.ROOT_DIR
    assert module.CRASH_LOG.name == expected_crash_log


def test_every_wrapper_points_at_a_distinct_crash_log() -> None:
    """No two wrappers must ever interleave their crash entries into the same file."""
    modules = [importlib.import_module(name) for name, _, _ in WRAPPERS]
    crash_logs = {str(m.CRASH_LOG) for m in modules}
    assert len(crash_logs) == len(modules)


def test_every_wrapper_targets_a_distinct_script() -> None:
    modules = [importlib.import_module(name) for name, _, _ in WRAPPERS]
    targets = {str(m.TARGET_SCRIPT) for m in modules}
    assert len(targets) == len(modules)
