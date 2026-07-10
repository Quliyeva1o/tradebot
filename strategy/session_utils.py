"""Shared session-time helpers for strategies ported from session-scoped Pine
Script indicators (e.g. AccumulationBreakoutStrategy, NasdaqMidlineSweepStrategy).

Bar.timestamp is confirmed UTC in this codebase (config/settings.py
Settings.TIMEZONE, default "UTC"; CSVDataProvider localizes/converts to it).
TradingView session strings (e.g. "0930-1100") are evaluated in the chart's
exchange/instrument timezone (commonly America/New_York for US-listed FX/
index instruments), not UTC -- so a fixed UTC offset would silently drift by
one hour across the DST transition. `is_in_session` converts each bar's UTC
timestamp to the given session timezone via `zoneinfo` before comparing
time-of-day, so the correct UTC-equivalent window is picked up per bar-date.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo


def is_in_session(
    timestamp: datetime, session_start: time, session_end: time, session_tz: ZoneInfo
) -> bool:
    """Whether timestamp falls in [session_start, session_end) in session_tz.

    Args:
        timestamp: A timezone-aware Bar timestamp (UTC in this codebase).
        session_start: Local time-of-day the session opens (inclusive).
        session_end: Local time-of-day the session closes (exclusive).
        session_tz: IANA zone (e.g. ZoneInfo("America/New_York")) the session
            times are defined in; timestamp is converted to this zone (DST-
            aware) before comparison.

    Returns:
        True if the bar's local time-of-day in session_tz falls in the range.
    """
    local_time = timestamp.astimezone(session_tz).time()
    return session_start <= local_time < session_end
