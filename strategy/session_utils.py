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

from core.models import Timeframe

TIMEFRAME_MINUTES: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.M30: 30,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
}


def add_minutes(t: time, minutes: int) -> time:
    """Adds `minutes` to a time-of-day, wrapping around midnight.

    Used to derive a session sub-window's end (e.g. an Opening Range's scan
    start) from its start time + a duration, without a full datetime.

    Args:
        t: The starting time-of-day.
        minutes: Minutes to add (any non-negative int).

    Returns:
        The resulting time-of-day, wrapped to [00:00:00, 24:00:00).
    """
    total_seconds = (t.hour * 3600 + t.minute * 60 + t.second) + minutes * 60
    total_seconds %= 24 * 3600
    return time(
        hour=total_seconds // 3600,
        minute=(total_seconds % 3600) // 60,
        second=total_seconds % 60,
        microsecond=t.microsecond,
    )


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


def session_length_in_bars(start: time, end: time, timeframe: Timeframe) -> int:
    """Number of `timeframe`-sized bars between two same-day local times.

    Used to turn a strategy's own configured session window into a bar count
    (e.g. for BacktestConfig.max_holding_bars), rather than assuming a fixed
    wall-clock session length that may not match the traded instrument's
    actual hours (CFDs/indices/metals often trade far longer than a classic
    NYSE cash session).

    Args:
        start: Local time-of-day the window opens (inclusive).
        end: Local time-of-day the window closes (exclusive). Must be later
            than `start` on the same calendar day (no overnight wraparound).
        timeframe: Bar timeframe the count is expressed in.

    Returns:
        The number of whole `timeframe` bars spanning [start, end).
    """
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    span_minutes = end_minutes - start_minutes
    if span_minutes <= 0:
        raise ValueError(f"end ({end}) must be later than start ({start}) on the same day")
    return span_minutes // TIMEFRAME_MINUTES[timeframe]
