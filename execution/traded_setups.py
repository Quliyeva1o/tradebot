"""Remembers which setups a live bot has already opened, so it never opens one twice.

The runners rebuild today's setup from scratch on every 2-minute poll and act on
it while it is inside SIGNAL_GRACE_MINUTES (see run_live_nasdaq_orb.py). Nothing
else knew the setup had already been traded. On Demo the stop lives at the broker
and removes the position the instant it is hit, so a stop inside the grace window
left the next poll with no position and the same setup still "fresh" -- and it
would open it again. Replayed on real 2026-09-11 XAUUSD bars (15m OR / 4R): the
stop was hit on the 14:02 UTC bar and the 14:04 poll saw the setup 4 bars old,
inside the 4-bar window. Paper never showed it, because paper closes on the next
poll, by which time the setup had expired.

Setup ids carry their own date, so the ledger needs no daily reset; it keeps only
the most recent entries.
"""

import json
from pathlib import Path

from utils.logging import setup_logger

logger = setup_logger("traded_setups")

MAX_REMEMBERED = 50


def already_traded(path: Path, setup_id: str) -> bool:
    """Whether `setup_id` has already been opened by the bot that owns `path`."""
    return setup_id in _read(path)


def record_traded(path: Path, setup_id: str) -> None:
    """Records `setup_id` as opened. Never raises (logs ERROR on failure)."""
    ids = [s for s in _read(path) if s != setup_id]
    ids.append(setup_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"setup_ids": ids[-MAX_REMEMBERED:]}))
    except Exception as exc:  # a bookkeeping write must never break order flow
        logger.error("Could not record traded setup %s in %s: %s", setup_id, path, type(exc).__name__)


def _read(path: Path) -> list[str]:
    """Fail-open: an unreadable ledger allows the trade rather than blocking every signal."""
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return []
    except Exception as exc:  # corrupt or unreadable -- degrade to the pre-ledger behaviour, visibly
        logger.error("Could not read %s (%s); treating it as empty.", path, type(exc).__name__)
        return []
    ids = data.get("setup_ids") if isinstance(data, dict) else None
    return [s for s in ids if isinstance(s, str)] if isinstance(ids, list) else []
