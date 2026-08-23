"""Live data-quality checks: gap/stale-bar detection (Sprint 6c, T3).

On the consumer side of MT5Connector.fetch_recent_bars(). Pure functions
over Bar sequences -- no MT5 dependency, no side effects.
Alerting is the caller's job (currently live_signal_check.py; a future live
TradeManager loop can call the same check_stale()/check_gaps() functions
before acting on freshly-fetched bars).

Both checks are weekend-aware: without this, a plain "is the latest bar
older than N bars' worth of time" check would false-positive every single
Friday evening through Sunday, when the market is legitimately closed, not
malfunctioning. The exact weekend window used here (closure begins Friday
at/after 20:00 UTC) matches the one already used by
run_research_campaign.py's validate_data_quality() for its own weekend-gap
exemption -- reproduced here rather than imported, since that script pulls
in heavy, unrelated dependencies (reportlab, matplotlib, MT5 download
orchestration) that have no business being on this lightweight live-
monitoring import path.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from core.models import Bar, Timeframe
from strategy.session_utils import TIMEFRAME_MINUTES


@dataclass(frozen=True)
class DataQualityIssue:
    """A single detected data-quality problem."""

    kind: str  # "stale" | "gap"
    message: str
    # A stable identity for THIS specific occurrence, unlike `message` (which
    # for "stale" embeds a staleness duration that grows every run and is
    # therefore never equal across repeated detections of the same
    # underlying event). Used by callers (see live_signal_check.py's
    # data-quality alert dedup) to recognize "the same issue as last run" vs.
    # a genuinely new one, without re-deriving detection internals or
    # string-parsing `message`. Deterministic and identical across repeated
    # detections of the same gap/stale event; changes only when the
    # underlying bars that caused it change.
    event_key: str


def _is_weekend_gap_start(prev_timestamp: datetime) -> bool:
    """Whether a gap starting at prev_timestamp is an expected weekend closure.

    Matches run_research_campaign.py's validate_data_quality(): a gap
    beginning on Friday (weekday 4) at/after 20:00 UTC is treated as the
    start of the expected weekend closure, not a data anomaly.
    """
    return prev_timestamp.weekday() == 4 and prev_timestamp.hour >= 20


def _is_daily_settlement_gap(prev_timestamp: datetime, current_timestamp: datetime) -> bool:
    """Whether a gap matches the broker's known daily settlement/rollover window.

    Confirmed via a live 4-week USTEC M5 bar-history sample (2026-06-26 to
    2026-07-22, 5000 bars) fetched directly from MT5 during the Sprint 7
    diagnostic sprint: bars stop at 23:55 UTC and resume at 01:00 UTC the
    next calendar day on EVERY weekday night without exception (14/14
    occurrences -- Mon/Tue/Wed/Thu nights; Friday night already falls under
    _is_weekend_gap_start's broader closure window, so there is no overlap
    between the two checks). This is USTEC's broker-side end-of-day
    swap/rollover pause, not a data-feed problem.

    Deliberately narrow (exact start/end time-of-day, not a general
    "any overnight gap is fine" relaxation) -- other gaps found in the same
    sample (e.g. a single 10:55-11:05 UTC gap, and a cluster of one-off
    midday gaps on 2026-06-29) did NOT recur at a consistent time and are
    correctly left flagged as genuine anomalies by this narrow match.
    """
    return (
        prev_timestamp.hour == 23
        and prev_timestamp.minute == 55
        and current_timestamp.hour == 1
        and current_timestamp.minute == 0
    )


def _is_weekend_now(now: datetime) -> bool:
    """Whether `now` falls within the expected weekend market closure.

    Friday (weekday 4) at/after 20:00 UTC through Sunday (weekday 6) before
    22:00 UTC -- the same window implied by _is_weekend_gap_start's Friday
    cutoff, extended through the Sunday reopen, so check_stale() does not
    false-positive every weekend while markets are legitimately closed.
    """
    weekday = now.weekday()
    if weekday == 4 and now.hour >= 20:
        return True
    if weekday == 5:
        return True
    return weekday == 6 and now.hour < 22


def check_stale(
    bars: list[Bar],
    timeframe: Timeframe,
    max_staleness_bars: float = 2.0,
    now: datetime | None = None,
) -> DataQualityIssue | None:
    """Flags if the most recently fetched bar is too old relative to now.

    A no-op (returns None) during the expected weekend market closure --
    see _is_weekend_now.

    Args:
        bars: Chronologically ordered (oldest first) bars, as returned by
            MT5Connector.fetch_recent_bars().
        timeframe: The bars' timeframe, used to derive the expected
            bar-to-bar interval.
        max_staleness_bars: How many bar-intervals old the latest bar may be
            before it's flagged (default 2.0 -- one interval of normal
            fetch/broker latency, plus a margin, not a hair-trigger on the
            first missed tick).
        now: The current time to compare against. Defaults to
            datetime.now(UTC); overridable for deterministic tests.

    Returns:
        A DataQualityIssue(kind="stale", ...) if the latest bar is older
        than max_staleness_bars * the timeframe's interval, or if `bars` is
        empty. None if the data is fresh (or now falls in the expected
        weekend closure).
    """
    now = now if now is not None else datetime.now(UTC)
    if _is_weekend_now(now):
        return None

    if not bars:
        return DataQualityIssue(
            kind="stale", message="No bars available to check staleness.", event_key="empty"
        )

    latest = bars[-1].timestamp
    expected_interval = timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    threshold = expected_interval * max_staleness_bars
    staleness = now - latest
    if staleness > threshold:
        return DataQualityIssue(
            kind="stale",
            message=(
                f"Latest bar ({latest.isoformat()}) is {staleness} old; "
                f"expected at most {threshold} ({max_staleness_bars} x {timeframe.value})."
            ),
            # The latest bar's own timestamp -- stable across repeated runs
            # as long as no new bar has arrived (the actual root cause of a
            # given staleness event), unlike `staleness` itself which grows
            # every run and would never repeat.
            event_key=latest.isoformat(),
        )
    return None


def check_gaps(
    bars: list[Bar],
    timeframe: Timeframe,
    max_gap_bars: float = 1.5,
) -> list[DataQualityIssue]:
    """Flags consecutive fetched bars whose timestamp gap exceeds expectation.

    Weekend gaps (see _is_weekend_gap_start) and the broker's daily
    settlement/rollover window (see _is_daily_settlement_gap) are excluded
    -- both are expected closures, not data anomalies.

    Args:
        bars: Chronologically ordered (oldest first) bars, as returned by
            MT5Connector.fetch_recent_bars().
        timeframe: The bars' timeframe, used to derive the expected
            bar-to-bar interval.
        max_gap_bars: How many bar-intervals apart two consecutive bars may
            be before the gap between them is flagged (default 1.5 -- bars
            should normally be exactly 1 interval apart).

    Returns:
        A DataQualityIssue(kind="gap", ...) per anomalous gap found (zero or
        more), in chronological order. Never raises on fewer than 2 bars --
        returns an empty list instead, since there is nothing to compare.
    """
    if len(bars) < 2:
        return []

    expected_interval = timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    threshold = expected_interval * max_gap_bars
    issues: list[DataQualityIssue] = []

    for i in range(1, len(bars)):
        prev_ts = bars[i - 1].timestamp
        current_ts = bars[i].timestamp
        gap = current_ts - prev_ts
        if gap <= threshold:
            continue
        if _is_weekend_gap_start(prev_ts):
            continue
        if _is_daily_settlement_gap(prev_ts, current_ts):
            continue
        issues.append(
            DataQualityIssue(
                kind="gap",
                message=(
                    f"Gap of {gap} between {prev_ts.isoformat()} and {current_ts.isoformat()} "
                    f"(expected ~{expected_interval})."
                ),
                # The exact bar pair that bounds the gap -- stable and
                # identical across repeated detections of the same gap
                # (e.g. one that stays within the lookback window for
                # several days).
                event_key=f"{prev_ts.isoformat()}_{current_ts.isoformat()}",
            )
        )

    return issues
