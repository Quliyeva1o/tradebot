#!/usr/bin/env python3
"""Crash-visibility supervisor for run_live_nasdaq_orb.py.

Thin per-script wrapper around notifications.crash_alert's shared logic --
see that module's docstring for the full design rationale (why this exists,
why it must stay import-minimal, why it invokes the target as a subprocess).

Usage (mirrors run_live_nasdaq_orb.py's own CLI exactly -- all args are
forwarded unchanged to the child process):
    python run_live_nasdaq_orb_with_crash_alert.py --symbol NAS100
"""

import sys
from pathlib import Path

from notifications.crash_alert import run_and_alert_on_crash

ROOT_DIR = Path(__file__).parent.resolve()
TARGET_SCRIPT = ROOT_DIR / "run_live_nasdaq_orb.py"
CRASH_LOG = ROOT_DIR / "logs" / "run_live_nasdaq_orb_crashes.log"

if __name__ == "__main__":
    sys.exit(run_and_alert_on_crash(TARGET_SCRIPT, CRASH_LOG, sys.argv[1:]))
