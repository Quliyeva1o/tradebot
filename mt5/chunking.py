"""Shared MT5 historical-data request chunking utilities.

MT5's `copy_rates_range()` fails outright (returns None) rather than truncating once a
single request would span too many bars (observed cutoff on this broker: succeeds at
~62k bars, fails above ~74k -- consistent with the classic MT5 65535-bar buffer limit).
Both `data/download_history.py` and `mt5/history_downloader.py` hit this same limit and
need to split a wide date range into safely-sized request windows. This module is the
single source of truth for that chunk-sizing logic so the two callers can't duplicate
(and potentially diverge on) it.
"""

from collections.abc import Iterator
from datetime import datetime, timedelta

TIMEFRAME_DELTA: dict[str, timedelta] = {
    "M1": timedelta(minutes=1),
    "M5": timedelta(minutes=5),
    "M15": timedelta(minutes=15),
    "M30": timedelta(minutes=30),
    "H1": timedelta(hours=1),
    "H4": timedelta(hours=4),
    "D1": timedelta(days=1),
}

CHUNK_TARGET_BARS = 40_000


def iter_chunk_windows(
    start: datetime, end: datetime, timeframe: str, chunk_target_bars: int = CHUNK_TARGET_BARS
) -> Iterator[tuple[datetime, datetime]]:
    """Splits [start, end] into windows sized to stay under MT5's per-request bar limit.

    Args:
        start: Overall range start (inclusive).
        end: Overall range end (inclusive).
        timeframe: One of TIMEFRAME_DELTA's keys, used to size each window.
        chunk_target_bars: Approximate number of bars per chunk window.

    Yields:
        (chunk_start, chunk_end) tuples covering [start, end] with no gaps.
    """
    span = TIMEFRAME_DELTA[timeframe] * chunk_target_bars
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + span, end)
        yield cursor, chunk_end
        cursor = chunk_end
