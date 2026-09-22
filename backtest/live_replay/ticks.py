"""Real MT5 ticks around one moment, cached on disk.

Only session-open stops need them: the first tick of a new week is a stale re-quote of the
previous close, and the real price lands seconds later -- which is why 2026-09-14's stops
filled at -3.1R while the bar's own open says -1R. Everything else is priced from bars.

MT5 timestamps (rates AND ticks) read as the broker's wall clock, not UTC -- see the BROKER_TZ
comment in mt5/rates.py. Requests and replies are converted on both sides here, on the clock of
the broker the ticks are fetched from (config/brokers.py SERVER_CLOCKS), passed in rather than
imported from mt5/rates.py, which loads MetaTrader5 and .env at import time.

Until 2026-09-22 that clock was Europe/Bucharest, an hour off the real one in the weeks when the
American and European clock changes disagree. A window fetched in those weeks asked for the wrong
hour and relabelled what came back as the right one, so cached files from those weeks hold ticks
from an hour away from their name.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from core.broker_clock import BrokerClock
from core.models import SignalDirection

TickRow = tuple[int, float, float]  # (epoch milliseconds UTC, bid, ask)
Fetch = Callable[[str, int, int], list[TickRow]]

# Probed on FundingPips-Trial 2026-09-15: copy_ticks_range returns nothing before these.
DEFAULT_TICK_HISTORY_START = datetime(2025, 3, 14, tzinfo=UTC)
TICK_HISTORY_START = {"XAUUSD": datetime(2026, 5, 5, tzinfo=UTC)}


def _to_broker_clock(epoch: int, clock: BrokerClock) -> datetime:
    """The datetime MT5 expects: broker wall clock, labelled UTC."""
    return datetime.fromtimestamp(epoch, clock).replace(tzinfo=UTC)


def _from_broker_clock(msc: int, clock: BrokerClock) -> int:
    """MT5's millisecond stamp (broker wall clock) as genuine UTC milliseconds."""
    naive = datetime.fromtimestamp(msc / 1000, UTC).replace(tzinfo=None)
    return int(naive.replace(tzinfo=clock).astimezone(UTC).timestamp() * 1000)


def mt5_fetch(symbol: str, start: int, end: int, clock: BrokerClock) -> list[TickRow]:
    """Ticks in [start, end) epoch seconds, from the terminal, whose server runs on `clock`. Read-only."""
    import MetaTrader5 as mt5  # noqa: N813 -- only needed when the cache misses

    from mt5.connector import MT5Connector

    if mt5.terminal_info() is None and not MT5Connector().connect():
        raise RuntimeError("MT5 is not reachable; cannot fill the tick cache")
    mt5.symbol_select(symbol, True)
    rows = mt5.copy_ticks_range(symbol, _to_broker_clock(start, clock), _to_broker_clock(end, clock),
                                mt5.COPY_TICKS_ALL)
    if rows is None:
        return []
    return [(_from_broker_clock(int(r["time_msc"]), clock), float(r["bid"]), float(r["ask"]))
            for r in rows]


class TickCache:
    """Serves tick windows from disk, fetching (once) only what is missing."""

    def __init__(self, cache_dir: Path, fetch: Fetch | None) -> None:
        """`fetch=None` serves the cache only: a miss returns no ticks and is NOT written.

        There is no default fetch: one needs the broker's clock, which only the caller knows --
        backtest/live_replay/brokers.tick_cache builds it.

        Every fetched window is cached for good, an empty one included. So a miss fetched from a
        terminal logged into the wrong broker -- which knows no such symbol and returns nothing --
        would be stored as "no ticks here" permanently. Callers that cannot vouch for the
        connected account pass None.
        """
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
        if self._fetch is None:
            return []
        rows = self._fetch(symbol, start, start + seconds)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["time_msc", "bid", "ask"])
            writer.writerows(rows)
        return rows

    def entry_quote(self, symbol: str, minute_start: int,
                    offset_seconds: int) -> tuple[float, float] | None:
        """(bid, ask) of the first tick at least `offset_seconds` into the poll's minute.

        The VPS polls fire a few seconds into the minute -- the real open deals were stamped
        :04 to :06 -- and the market order fills at that moment's quote, not at the bar's open.
        On the ten 2026-09-10..14 Demo entries this matched nine within 2.5 points; the bar
        open missed by up to 15.
        """
        if not self.has_history(symbol, minute_start):
            return None
        at = (minute_start + offset_seconds) * 1000
        for time_msc, bid, ask in self.window(symbol, minute_start, 60):
            if time_msc >= at:
                return bid, ask
        return None

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
