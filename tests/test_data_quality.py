"""Unit tests for core/data_quality.py (Sprint 6c, T3: gap/stale-bar detection).

No MT5 dependency -- pure functions over Bar sequences.
"""

from datetime import UTC, datetime, timedelta

from core.data_quality import check_gaps, check_stale
from core.models import Bar, Timeframe


def _bar(ts: datetime) -> Bar:
    return Bar(timestamp=ts, open=100.0, high=100.5, low=99.5, close=100.0, volume=10.0)


class TestCheckStale:
    """Tests for check_stale()."""

    def test_fresh_bar_returns_none(self) -> None:
        # Monday, well inside normal trading hours.
        now = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
        bars = [_bar(now - timedelta(minutes=3))]
        assert check_stale(bars, Timeframe.M5, now=now) is None

    def test_stale_bar_returns_an_issue(self) -> None:
        now = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
        bars = [_bar(now - timedelta(minutes=30))]  # 6x the M5 interval
        issue = check_stale(bars, Timeframe.M5, now=now)
        assert issue is not None
        assert issue.kind == "stale"
        assert "30" in issue.message or "0:30:00" in issue.message

    def test_exactly_at_threshold_is_not_stale(self) -> None:
        now = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
        # max_staleness_bars=2.0 (default) * M5 (5 min) = 10 min threshold, inclusive.
        bars = [_bar(now - timedelta(minutes=10))]
        assert check_stale(bars, Timeframe.M5, now=now) is None

    def test_just_past_threshold_is_stale(self) -> None:
        now = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
        bars = [_bar(now - timedelta(minutes=10, seconds=1))]
        assert check_stale(bars, Timeframe.M5, now=now) is not None

    def test_empty_bars_returns_an_issue(self) -> None:
        now = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)  # Monday -- not a weekend no-op
        issue = check_stale([], Timeframe.M5, now=now)
        assert issue is not None
        assert issue.kind == "stale"

    def test_custom_max_staleness_bars_threshold(self) -> None:
        now = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
        bars = [_bar(now - timedelta(minutes=12))]
        assert check_stale(bars, Timeframe.M5, max_staleness_bars=2.0, now=now) is not None
        assert check_stale(bars, Timeframe.M5, max_staleness_bars=3.0, now=now) is None

    def test_weekend_now_is_a_no_op_even_with_old_bars(self) -> None:
        # Saturday: market legitimately closed.
        now = datetime(2026, 1, 3, 12, 0, tzinfo=UTC)
        bars = [_bar(now - timedelta(days=1))]
        assert check_stale(bars, Timeframe.M5, now=now) is None

    def test_friday_evening_is_a_no_op(self) -> None:
        now = datetime(2026, 1, 2, 21, 0, tzinfo=UTC)  # Friday 21:00 UTC
        bars = [_bar(now - timedelta(hours=5))]
        assert check_stale(bars, Timeframe.M5, now=now) is None

    def test_friday_before_20_utc_still_checked_normally(self) -> None:
        now = datetime(2026, 1, 2, 19, 0, tzinfo=UTC)  # Friday 19:00 UTC -- still trading
        bars = [_bar(now - timedelta(hours=1))]
        assert check_stale(bars, Timeframe.M5, now=now) is not None

    def test_sunday_before_reopen_is_a_no_op(self) -> None:
        now = datetime(2026, 1, 4, 20, 0, tzinfo=UTC)  # Sunday 20:00 UTC -- still closed
        bars = [_bar(now - timedelta(hours=2))]
        assert check_stale(bars, Timeframe.M5, now=now) is None

    def test_sunday_after_reopen_checked_normally(self) -> None:
        now = datetime(2026, 1, 4, 23, 0, tzinfo=UTC)  # Sunday 23:00 UTC -- reopened
        bars = [_bar(now - timedelta(hours=2))]
        assert check_stale(bars, Timeframe.M5, now=now) is not None


class TestCheckGaps:
    """Tests for check_gaps()."""

    def test_no_gaps_returns_empty_list(self) -> None:
        base = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
        bars = [_bar(base + timedelta(minutes=5 * i)) for i in range(10)]
        assert check_gaps(bars, Timeframe.M5) == []

    def test_single_bar_returns_empty_list(self) -> None:
        assert check_gaps([_bar(datetime(2026, 1, 5, 9, 0, tzinfo=UTC))], Timeframe.M5) == []

    def test_empty_bars_returns_empty_list(self) -> None:
        assert check_gaps([], Timeframe.M5) == []

    def test_intraday_gap_is_flagged(self) -> None:
        base = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)  # Monday
        bars = [_bar(base), _bar(base + timedelta(minutes=30))]  # 6x expected interval
        issues = check_gaps(bars, Timeframe.M5)
        assert len(issues) == 1
        assert issues[0].kind == "gap"

    def test_exactly_at_threshold_is_not_a_gap(self) -> None:
        base = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
        # max_gap_bars=1.5 (default) * M5 (5 min) = 7.5 min threshold, inclusive.
        bars = [_bar(base), _bar(base + timedelta(minutes=7, seconds=30))]
        assert check_gaps(bars, Timeframe.M5) == []

    def test_multiple_gaps_are_all_reported(self) -> None:
        base = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
        bars = [
            _bar(base),
            _bar(base + timedelta(minutes=30)),  # gap 1
            _bar(base + timedelta(minutes=35)),
            _bar(base + timedelta(minutes=70)),  # gap 2
        ]
        issues = check_gaps(bars, Timeframe.M5)
        assert len(issues) == 2

    def test_weekend_gap_is_not_flagged(self) -> None:
        friday_close = datetime(2026, 1, 2, 21, 0, tzinfo=UTC)  # Friday 21:00 UTC
        sunday_reopen = datetime(2026, 1, 4, 22, 5, tzinfo=UTC)  # Sunday 22:05 UTC
        bars = [_bar(friday_close), _bar(sunday_reopen)]
        assert check_gaps(bars, Timeframe.M5) == []

    def test_gap_starting_before_friday_20_utc_is_still_flagged(self) -> None:
        friday_afternoon = datetime(2026, 1, 2, 15, 0, tzinfo=UTC)  # Friday 15:00 UTC
        later = friday_afternoon + timedelta(minutes=30)
        bars = [_bar(friday_afternoon), _bar(later)]
        assert len(check_gaps(bars, Timeframe.M5)) == 1

    def test_custom_max_gap_bars_threshold(self) -> None:
        base = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
        bars = [_bar(base), _bar(base + timedelta(minutes=12))]
        assert len(check_gaps(bars, Timeframe.M5, max_gap_bars=2.0)) == 1
        assert check_gaps(bars, Timeframe.M5, max_gap_bars=3.0) == []


class TestDailySettlementGapExemption:
    """Sprint 7 diagnostic: the broker's confirmed daily settlement window.

    A live 4-week USTEC bar-history sample showed the broker pausing EVERY
    weekday night from 23:55 to 01:00 UTC (settlement/rollover), 14/14
    occurrences with zero exceptions -- see _is_daily_settlement_gap()'s
    docstring in core/data_quality.py.
    """

    def test_daily_settlement_gap_is_not_flagged(self) -> None:
        monday_late = datetime(2026, 1, 5, 23, 55, tzinfo=UTC)  # Monday 23:55 UTC
        tuesday_reopen = datetime(2026, 1, 6, 1, 0, tzinfo=UTC)  # Tuesday 01:00 UTC
        bars = [_bar(monday_late), _bar(tuesday_reopen)]
        assert check_gaps(bars, Timeframe.M5) == []

    def test_recurs_identically_on_every_weekday_night(self) -> None:
        """Mon-Thu nights are all exempt.

        Fri->Sat is already covered by the weekend exemption, tested
        separately.
        """
        base = datetime(2026, 1, 5, 23, 55, tzinfo=UTC)  # Monday
        for day_offset in range(4):  # Mon->Tue, Tue->Wed, Wed->Thu, Thu->Fri
            prev = base + timedelta(days=day_offset)
            nxt = prev + timedelta(hours=1, minutes=5)
            assert check_gaps([_bar(prev), _bar(nxt)], Timeframe.M5) == [], (
                f"expected no gap flagged for {prev} -> {nxt}"
            )

    def test_gap_ending_before_01_00_is_still_flagged(self) -> None:
        """An early resume does not match the narrow exemption.

        A gap that starts at the settlement time but resumes EARLY (not at
        the confirmed 01:00 reopen) is not exempt.
        """
        monday_late = datetime(2026, 1, 5, 23, 55, tzinfo=UTC)
        early_reopen = datetime(2026, 1, 6, 0, 30, tzinfo=UTC)
        bars = [_bar(monday_late), _bar(early_reopen)]
        assert len(check_gaps(bars, Timeframe.M5)) == 1

    def test_gap_starting_a_few_minutes_before_settlement_is_still_flagged(self) -> None:
        """Not a loosened "any late-night gap is fine" rule.

        The start time must match exactly.
        """
        slightly_earlier = datetime(2026, 1, 5, 23, 50, tzinfo=UTC)
        tuesday_reopen = datetime(2026, 1, 6, 1, 0, tzinfo=UTC)
        bars = [_bar(slightly_earlier), _bar(tuesday_reopen)]
        assert len(check_gaps(bars, Timeframe.M5)) == 1

    def test_unrelated_intraday_gap_at_a_different_time_is_still_flagged(self) -> None:
        """Confirms the exemption doesn't accidentally swallow a genuine gap.

        E.g. the real 10:55-11:05 UTC gap found in the same diagnostic
        sample, which did NOT recur elsewhere and is a one-off intermittent
        issue, not a pattern.
        """
        base = datetime(2026, 1, 6, 10, 55, tzinfo=UTC)  # Tuesday 10:55 UTC
        bars = [_bar(base), _bar(base + timedelta(minutes=10))]
        assert len(check_gaps(bars, Timeframe.M5)) == 1
