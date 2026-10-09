"""Telegram alerts for the Demo runners' trade events.

Sends to DEMO_TG_CHAT_ID (and DEMO_TG_TOKEN, else the ICT bot's ICT_TG_TOKEN, the only Telegram bot in .env that works).
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
        # DEMO_TG_* is the bots' own chat; until it is set the alerts fall back to the ICT chat.
        token = env.get("DEMO_TG_TOKEN") or env.get("ICT_TG_TOKEN", "")
        chat = env.get("DEMO_TG_CHAT_ID") or env.get("ICT_TG_CHAT_ID", "")
        if not (token and chat):
            return
        try:
            from config.brokers import local
            title = f"{title} [{local().name}]"
        except Exception:   # noqa: BLE001  -- an unlabeled alert beats none
            pass
        lines = [title] + [f"{k}: {fields[k]}" for k in _FIELDS if fields.get(k) is not None]
        send(token, chat, "\n".join(lines))
    except Exception as exc:   # noqa: BLE001
        logger.warning("Telegram alert failed: %s", type(exc).__name__)
