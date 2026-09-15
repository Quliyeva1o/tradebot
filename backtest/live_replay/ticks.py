"""Real MT5 ticks around one moment, cached on disk.

Only session-open stops need them: the first tick of a new week is a stale re-quote of the
previous close, and the real price lands seconds later -- which is why 2026-09-14's stops
filled at -3.1R while the bar's own open says -1R. Everything else is priced from bars.

MT5 timestamps (rates AND ticks) read as the broker's wall clock, not UTC -- see the BROKER_TZ
comment in mt5/rates.py. Requests and replies are converted on both sides here. BROKER_TZ is
defined locally rather than imported from mt5/rates.py, which loads MetaTrader5 and .env at
import time.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.models import SignalDirection

BROKER_TZ = ZoneInfo("Europe/Bucharest")
TickRow = tuple[int, float, float]  # (epoch milliseconds UTC, bid, ask)
Fetch = Callable[[str, int, int], list[TickRow]]

# Probed on FundingPips-Trial 2026-09-15: copy_ticks_range returns nothing before these.
DEFAULT_TICK_HISTORY_START = datetime(2025, 3, 14, tzinfo=UTC)
TICK_HISTORY_START = {"XAUUSD": datetime(2026, 5, 5, tzinfo=UTC)}


def _to_broker_clock(epoch: int) -> datetime:
    """The datetime MT5 expects: broker wall clock, labelled UTC."""
    return datetime.fromtimestamp(epoch, BROKER_TZ).replace(tzinfo=UTC)


def _from_broker_clock(msc: int) -> int:
    """MT5's millisecond stamp (broker wall clock) as genuine UTC milliseconds."""
    naive = datetime.fromtimestamp(msc / 1000, UTC).replace(tzinfo=None)
    return int(naive.replace(tzinfo=BROKER_TZ).astimezone(UTC).timestamp() * 1000)


def mt5_fetch(symbol: str, start: int, end: int) -> list[TickRow]:
    """Ticks in [start, end) epoch seconds, from the terminal. Read-only."""
    import MetaTrader5 as mt5  # noqa: N813 -- only needed when the cache misses

    from mt5.connector import MT5Connector

    if mt5.terminal_info() is None and not MT5Connector().connect():
        raise RuntimeError("MT5 is not reachable; cannot fill the tick cache")
    mt5.symbol_select(symbol, True)
    rows = mt5.copy_ticks_range(symbol, _to_broker_clock(start), _to_broker_clock(end),
                                mt5.COPY_TICKS_ALL)
    if rows is None:
        return []
    return [(_from_broker_clock(int(r["time_msc"])), float(r["bid"]), float(r["ask"])) for r in rows]


class TickCache:
    """Serves tick windows from disk, fetching (once) only what is missing."""

    def __init__(self, cache_dir: Path, fetch: Fetch = mt5_fetch) -> None:
        self._dir = Path(cache_dir)
        self._fetch = fetch

    def has_history(self, symbol: str, epoch: int) -> bool:
        start = TICK_HISTORY_START.get(symbol, DEFAULT_TICK_HISTORY_START)
        return epoch >= int(start.timestamp())

    def window(self, symbol: str, start: int, seconds: int) -> list[TickRow]:
        path = self._dir / symbol / f"{start}_{seconds}.csv"
        if path.exists():
            with path.open(newline="", encoding="utf-8") as handle:
                return [(int(r["time_msc"]), float(r["bid"]), float(r["ask"]))
                        for r in csv.DictReader(handle)]
        rows = self._fetch(symbol, start, start + seconds)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time_msc", "bid", "ask"])
            writer.writerows(rows)
        return rows

    def first_crossing(self, symbol: str, start: int, direction: SignalDirection, level: float,
                       seconds: int = 300) -> float | None:
        """The price of the first tick that reaches `level`, or None when there is no tick history."""
        if not self.has_history(symbol, start):
            return None
        for _, bid, ask in self.window(symbol, start, seconds):
            if direction == SignalDirection.BUY and bid <= level:
                return bid
            if direction == SignalDirection.SELL and ask >= level:
                return ask
        return None
