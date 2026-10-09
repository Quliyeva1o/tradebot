"""Telegram alerts for the Demo runners' trade events.

Uses the ICT bot's token (ICT_TG_TOKEN / ICT_TG_CHAT_ID in .env), the only Telegram bot there that works.
Sending never raises: an alert must not break a poll.
"""
from __future__ import annotations

import os
from pathlib import Path

from utils.logging import setup_logger

logger = setup_logger("demo_alert")

_FIELDS = ("symbol", "direction", "fill_price", "stop_loss", "take_profit", "outcome", "reason")


def send_alert(title: str, fields: dict) -> None:
    if "PYTEST_CURRENT_TEST" in os.environ:   # the runners' module default mode is "live"; tests must stay silent
        return
    try:
        from dotenv import dotenv_values

        from ict_lab.live import send
        env = dotenv_values(Path(__file__).resolve().parent.parent / ".env")
        token, chat = env.get("ICT_TG_TOKEN", ""), env.get("ICT_TG_CHAT_ID", "")
        if not (token and chat):
            return
        lines = [title] + [f"{k}: {fields[k]}" for k in _FIELDS if fields.get(k) is not None]
        send(token, chat, "\n".join(lines))
    except Exception as exc:   # noqa: BLE001
        logger.warning("Telegram alert failed: %s", type(exc).__name__)
