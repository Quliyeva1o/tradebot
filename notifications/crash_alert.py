"""Shared crash-visibility supervisor logic for every run_live_*.py's
crash-alert wrapper script.

Root cause this exists for (originally documented in
run_live_demo_with_crash_alert.py, generalized here): a run_live_*.py
script's own imports (MetaTrader5, and anything it pulls in transitively,
e.g. numpy) happen BEFORE its logger is configured. An import-time failure
there -- e.g. a OneDrive-locked/corrupted .venv binary, plausible given this
project lives under a commonly-synced Desktop path -- crashes the whole
process with a raw traceback on stderr and a non-zero exit code, and NOTHING
is ever written to any log file, since the logger object doesn't exist yet.
Task Scheduler records the failed run in its own history, but nothing
alerts a human, and no log line explains what happened.

Design: the target script is invoked as a SEPARATE OS subprocess, not
imported directly, so a crash inside it (at import time or any other point)
can always be observed from out here via its exit code and captured
stdout/stderr -- regardless of whether ITS OWN logger ever got to run.

Deliberately minimal, disjoint imports (subprocess, datetime, pathlib, plus
config.settings/notifications.telegram -- neither of which touches
MetaTrader5 or numpy) so THIS module's own ability to run and report a
crash is never at risk from the exact binary-locking failure it exists to
detect in the child process. Every thin per-script wrapper that calls into
this module (see run_live_demo_with_crash_alert.py and its siblings) must
keep that same minimal import list for the same reason.
"""

import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from config.settings import Settings
from notifications.telegram import TelegramNotifier

_TRACEBACK_MARKER = "Traceback (most recent call last):"


def is_crash(returncode: int, stderr: str) -> bool:
    """Whether the wrapped run counts as a crash.

    Primarily the exit code (any Python uncaught exception, at import time
    or anywhere else, always exits non-zero) -- the stderr marker is a
    belt-and-suspenders fallback for the unusual case of a traceback having
    been printed but somehow not propagated to a non-zero exit code.
    """
    return returncode != 0 or _TRACEBACK_MARKER in stderr


def run_and_alert_on_crash(target_script: Path, crash_log: Path, argv: list[str]) -> int:
    """Runs target_script as a subprocess; on any crash, logs + alerts.

    On a normal (non-crash) run, this is a pure passthrough: no extra log
    entry, no Telegram message, nothing beyond forwarding the child's own
    exit code.

    Args:
        target_script: The run_live_*.py script to supervise.
        crash_log: Where to append a timestamped crash entry (full captured
            stdout/stderr) on a crash. Distinct per target_script so
            multiple supervised scripts never interleave in one file.
        argv: Extra CLI args forwarded to target_script unchanged (e.g.
            ["--symbol", "USTEC", "--timeframe", "M5"]).

    Returns:
        The wrapped subprocess's own exit code, unchanged -- Task
        Scheduler's own "Last Run Result" still accurately reflects
        success/failure regardless of this wrapper's own alerting.
    """
    command = [sys.executable, str(target_script), *argv]

    result = subprocess.run(command, capture_output=True, text=True, cwd=target_script.parent)

    if is_crash(result.returncode, result.stderr):
        _record_crash(target_script.name, crash_log, result.returncode, result.stdout, result.stderr)

    return result.returncode


def _record_crash(script_name: str, crash_log: Path, returncode: int, stdout: str, stderr: str) -> None:
    """Best-effort crash log entry + Telegram alert. Never raises."""
    timestamp = datetime.now(UTC).isoformat()
    _append_crash_log(crash_log, script_name, timestamp, returncode, stdout, stderr)
    _send_crash_alert(script_name, crash_log, timestamp, returncode)


def _append_crash_log(
    crash_log: Path, script_name: str, timestamp: str, returncode: int, stdout: str, stderr: str
) -> None:
    """Appends one timestamped crash entry with the full captured output.

    Best-effort: a write failure here (e.g. the same disk/sync issue that
    caused the crash) must never prevent the Telegram alert from still
    being attempted separately.
    """
    entry = (
        f"{'=' * 70}\n"
        f"{timestamp} -- {script_name} CRASHED (exit code {returncode})\n"
        f"{'=' * 70}\n"
        f"--- stdout ---\n{stdout}\n"
        f"--- stderr ---\n{stderr}\n\n"
    )
    try:
        crash_log.parent.mkdir(parents=True, exist_ok=True)
        with crash_log.open("a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as exc:  # the crash-alert path itself must never crash
        print(f"Could not write crash log: {type(exc).__name__}", file=sys.stderr)


def _send_crash_alert(script_name: str, crash_log: Path, timestamp: str, returncode: int) -> None:
    """Best-effort Telegram alert -- short, points at the crash log file.

    Deliberately does NOT include the captured stdout/stderr: a full
    traceback dumped into Telegram is exactly the noise this alert should
    avoid -- just enough to send a human to the crash log file.
    """
    try:
        settings = Settings.load()
        notifier = TelegramNotifier(settings.TELEGRAM_TOKEN, settings.TELEGRAM_CHAT_ID)
        notifier.send_message(
            f"\U0001f6a8 {script_name} CRASHED at {timestamp} (exit code {returncode}) "
            f"-- see {crash_log}"
        )
    except Exception as exc:  # notification is best-effort, must never affect the caller
        print(f"Could not send crash Telegram alert: {type(exc).__name__}", file=sys.stderr)
