"""File-based kill-switch for halting live trading.

Phase 6 (order-sending) infrastructure -- there is no order-sending code
yet, so nothing currently acts on is_trading_halted() except
live_signal_check.py's read-only, purely informational status check.

Design: is_trading_halted() holds NO hidden state of its own -- it is a
pure risk/kill_switch.flag existence check, so a human can halt or resume
trading immediately by creating or deleting that one file, with nothing
else to reason about. There is deliberately no deactivate()/clear()
function: only a human, on the filesystem, can remove the flag. A
kill-switch that code could programmatically clear would defeat its
purpose.
"""

from datetime import UTC, datetime
from pathlib import Path

from config.settings import Settings
from notifications.telegram import TelegramNotifier
from utils.logging import setup_logger

logger = setup_logger("kill_switch")

KILL_SWITCH_FLAG = Path(__file__).parent / "kill_switch.flag"


def is_trading_halted() -> bool:
    """Whether the kill-switch is active.

    Returns:
        True if risk/kill_switch.flag exists, False otherwise.
    """
    return KILL_SWITCH_FLAG.exists()


def activate_kill_switch(reason: str) -> None:
    """Creates the kill-switch flag and sends a Telegram alert, if not already active.

    Idempotent: a no-op if the flag already exists. DailyRiskTracker calls
    this once per cycle for as long as the daily loss limit stays breached
    (e.g. every 5 minutes from a future order-engine loop), so without this
    guard every subsequent cycle would re-send the same Telegram alert for
    an already-reported breach.

    Args:
        reason: Human-readable explanation, written into the flag file's
            contents (for a human who finds it later) and into the Telegram
            alert.
    """
    if is_trading_halted():
        return

    timestamp = datetime.now(UTC).isoformat()
    try:
        KILL_SWITCH_FLAG.parent.mkdir(parents=True, exist_ok=True)
        KILL_SWITCH_FLAG.write_text(f"{timestamp} {reason}\n")
    except Exception as exc:  # noqa: BLE001 - the flag write is the actual halt; must never silently vanish
        logger.error("Could not write kill-switch flag %s: %s", KILL_SWITCH_FLAG, type(exc).__name__)
        return

    logger.error("KILL SWITCH ACTIVATED: %s", reason)
    _send_kill_switch_alert(reason, timestamp)


def _send_kill_switch_alert(reason: str, timestamp: str) -> None:
    """Best-effort Telegram notification for a kill-switch activation.

    Never raises: same contract as live_signal_check.send_telegram_alert --
    a misconfigured or unreachable Telegram integration must never affect
    the caller. Any failure is logged and swallowed here.
    """
    try:
        settings = Settings.load()
        notifier = TelegramNotifier(settings.TELEGRAM_TOKEN, settings.TELEGRAM_CHAT_ID)
        message = (
            "⚠️ RISK LIMIT HIT\n"
            "Kill-switch ACTIVATED\n"
            f"Reason: {reason}\n"
            f"Time: {timestamp}\n"
            "Trading halted until risk/kill_switch.flag is manually removed."
        )
        notifier.send_message(message)
    except Exception as exc:  # noqa: BLE001 - notification is best-effort, must never affect the caller
        logger.warning("Kill-switch Telegram alert failed: %s", type(exc).__name__)
