#!/usr/bin/env python3
"""Crash-visibility supervisor for run_live_demo.py (Sprint 9).

Root cause this exists for: run_live_demo.py's own imports (MetaTrader5,
and anything it pulls in transitively, e.g. numpy) happen BEFORE its logger
is configured (see run_live_demo.py's module-level `logger = setup_logger(...)`
line). An import-time failure there -- e.g. a OneDrive-locked/corrupted
.venv binary, plausible given this project lives under a commonly-synced
Desktop path -- crashes the whole process with a raw traceback on stderr and
a non-zero exit code, and NOTHING is ever written to any log file, since the
logger object doesn't exist yet. Task Scheduler records the failed run in
its own history, but nothing alerts a human, and no log line explains what
happened -- exactly the failure mode that let a real trade signal go
unacted-on for hours with zero trace.

Design: run_live_demo.py is invoked as a SEPARATE OS subprocess, not
imported directly, so a crash inside it (at import time or any other point)
can always be observed from out here via its exit code and captured
stdout/stderr -- regardless of whether ITS OWN logger ever got to run.

Deliberately minimal, disjoint imports (subprocess, sys, datetime, pathlib,
plus config.settings/notifications.telegram -- neither of which touches
MetaTrader5 or numpy) so THIS script's own ability to start and report a
crash is never at risk from the exact binary-locking failure it exists to
detect in the child process. If this script imported MetaTrader5/numpy
itself, it would be just as vulnerable to the same failure, defeating the
whole point.

Usage (mirrors run_live_demo.py's own CLI exactly -- all args are forwarded
unchanged to the child process):
    python run_live_demo_with_crash_alert.py --symbol USTEC --timeframe M5
"""

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from config.settings import Settings
from notifications.telegram import TelegramNotifier

ROOT_DIR = Path(__file__).parent.resolve()
TARGET_SCRIPT = ROOT_DIR / "run_live_demo.py"
CRASH_LOG = ROOT_DIR / "logs" / "run_live_demo_crashes.log"

_TRACEBACK_MARKER = "Traceback (most recent call last):"


def _is_crash(returncode: int, stderr: str) -> bool:
    """Whether the wrapped run counts as a crash.

    Primarily the exit code (any Python uncaught exception, at import time
    or anywhere else, always exits non-zero) -- the stderr marker is a
    belt-and-suspenders fallback for the unusual case of a traceback having
    been printed but somehow not propagated to a non-zero exit code.
    """
    return returncode != 0 or _TRACEBACK_MARKER in stderr


def run_and_alert_on_crash(argv: list[str] | None = None) -> int:
    """Runs run_live_demo.py as a subprocess; on any crash, logs + alerts.

    On a normal (non-crash) run, this is a pure passthrough: no extra log
    entry, no Telegram message, nothing beyond forwarding the child's own
    exit code.

    Args:
        argv: Extra CLI args forwarded to run_live_demo.py (e.g.
            ["--symbol", "USTEC", "--timeframe", "M5"]). Defaults to this
            process's own argv[1:] when run as a script.

    Returns:
        The wrapped subprocess's own exit code, unchanged -- Task
        Scheduler's own "Last Run Result" still accurately reflects
        success/failure regardless of this wrapper's own alerting.
    """
    argv = argv if argv is not None else sys.argv[1:]
    command = [sys.executable, str(TARGET_SCRIPT), *argv]

    result = subprocess.run(command, capture_output=True, text=True, cwd=ROOT_DIR)

    if _is_crash(result.returncode, result.stderr):
        _record_crash(result.returncode, result.stdout, result.stderr)

    return result.returncode


def _record_crash(returncode: int, stdout: str, stderr: str) -> None:
    """Best-effort crash log entry + Telegram alert. Never raises."""
    timestamp = datetime.now(UTC).isoformat()
    _append_crash_log(timestamp, returncode, stdout, stderr)
    _send_crash_alert(timestamp, returncode)


def _append_crash_log(timestamp: str, returncode: int, stdout: str, stderr: str) -> None:
    """Appends one timestamped crash entry with the full captured output.

    Best-effort: a write failure here (e.g. the same disk/sync issue that
    caused the crash) must never prevent the Telegram alert from still
    being attempted separately.
    """
    entry = (
        f"{'=' * 70}\n"
        f"{timestamp} -- run_live_demo.py CRASHED (exit code {returncode})\n"
        f"{'=' * 70}\n"
        f"--- stdout ---\n{stdout}\n"
        f"--- stderr ---\n{stderr}\n\n"
    )
    try:
        CRASH_LOG.parent.mkdir(parents=True, exist_ok=True)
        with CRASH_LOG.open("a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as exc:  # the crash-alert path itself must never crash
        print(f"Could not write crash log: {type(exc).__name__}", file=sys.stderr)


def _send_crash_alert(timestamp: str, returncode: int) -> None:
    """Best-effort Telegram alert -- short, points at the crash log file.

    Deliberately does NOT include the captured stdout/stderr: a full
    traceback dumped into Telegram is exactly the noise this alert should
    avoid -- just enough to send a human to logs/run_live_demo_crashes.log.
    """
    try:
        settings = Settings.load()
        notifier = TelegramNotifier(settings.TELEGRAM_TOKEN, settings.TELEGRAM_CHAT_ID)
        notifier.send_message(
            "\U0001f6a8 run_live_demo.py CRASHED at "
            f"{timestamp} (exit code {returncode}) -- see logs/run_live_demo_crashes.log"
        )
    except Exception as exc:  # notification is best-effort, must never affect the caller
        print(f"Could not send crash Telegram alert: {type(exc).__name__}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(run_and_alert_on_crash())
