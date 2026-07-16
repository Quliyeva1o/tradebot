"""Unit tests for research/turn_of_month_study.py (event-study, not a live strategy)."""

from datetime import datetime

import pytest

from core.models import Bar
from research.turn_of_month_study import (
    MonthTurnEvent,
    compute_summary,
    find_month_turn_events,
    run_study,
)


def _daily_bar(date_str: str, close: float) -> Bar:
    ts = datetime.strptime(date_str, "%Y-%m-%d")
    return Bar(timestamp=ts, open=close, high=close, low=close, close=close, volume=100.0)


@pytest.fixture
def two_month_turn_bars() -> list[Bar]:
    """Two month-turn events: Jan30->Feb1 (event 1) and Feb26->Mar1 (event 2)."""
    return [
        _daily_bar("2026-01-28", 100.0),
        _daily_bar("2026-01-29", 101.0),
        _daily_bar("2026-01-30", 103.0),  # day -1 for event 1
        _daily_bar("2026-02-01", 105.0),  # day +1
        _daily_bar("2026-02-02", 107.0),  # day +2
        _daily_bar("2026-02-03", 104.0),  # day +3
        _daily_bar("2026-02-04", 108.0),
        _daily_bar("2026-02-26", 110.0),  # day -1 for event 2
        _daily_bar("2026-03-01", 112.0),  # day +1
        _daily_bar("2026-03-02", 111.0),  # day +2
        _daily_bar("2026-03-03", 115.0),  # day +3
    ]


class TestFindMonthTurnEvents:
    def test_finds_both_events_for_hold_days_1(self, two_month_turn_bars: list[Bar]) -> None:
        events, skipped_insufficient, skipped_gap = find_month_turn_events(
            two_month_turn_bars, hold_days=1
        )

        assert skipped_insufficient == 0
        assert skipped_gap == 0
        assert len(events) == 2

        assert events[0].day_minus_1_date == datetime(2026, 1, 30)
        assert events[0].day_plus_n_date == datetime(2026, 2, 1)
        assert events[0].entry_close == 103.0
        assert events[0].exit_close == 105.0
        assert events[0].return_pct == pytest.approx((105.0 - 103.0) / 103.0 * 100.0)

        assert events[1].day_minus_1_date == datetime(2026, 2, 26)
        assert events[1].day_plus_n_date == datetime(2026, 3, 1)
        assert events[1].return_pct == pytest.approx((112.0 - 110.0) / 110.0 * 100.0)

    def test_finds_both_events_for_hold_days_3(self, two_month_turn_bars: list[Bar]) -> None:
        events, skipped_insufficient, skipped_gap = find_month_turn_events(
            two_month_turn_bars, hold_days=3
        )

        assert skipped_insufficient == 0
        assert len(events) == 2

        assert events[0].day_plus_n_date == datetime(2026, 2, 3)
        assert events[0].return_pct == pytest.approx((104.0 - 103.0) / 103.0 * 100.0)

        assert events[1].day_plus_n_date == datetime(2026, 3, 3)
        assert events[1].return_pct == pytest.approx((115.0 - 110.0) / 110.0 * 100.0)

    def test_skips_event_with_insufficient_forward_data(self, two_month_turn_bars: list[Bar]) -> None:
        """hold_days=5 pushes event 2's exit index (Feb26 index + 5) past the end of the series."""
        events, skipped_insufficient, skipped_gap = find_month_turn_events(
            two_month_turn_bars, hold_days=5
        )

        assert len(events) == 1  # only event 1 has 5 forward bars available
        assert skipped_insufficient == 1
        assert skipped_gap == 0

    def test_skips_large_gap_as_data_irregularity(self) -> None:
        """A gap far exceeding max_gap_days (e.g. a missing month of history) must not be
        misattributed as a genuine month-turn event.
        """
        bars = [
            _daily_bar("2026-01-30", 100.0),
            _daily_bar("2026-04-01", 105.0),  # ~61 calendar days later
            _daily_bar("2026-04-02", 106.0),
        ]

        events, skipped_insufficient, skipped_gap = find_month_turn_events(
            bars, hold_days=1, max_gap_days=10
        )

        assert events == []
        assert skipped_gap == 1
        assert skipped_insufficient == 0

    def test_empty_and_single_bar_lists_produce_no_events(self) -> None:
        events, skipped_insufficient, skipped_gap = find_month_turn_events([], hold_days=1)
        assert events == []
        assert skipped_insufficient == 0
        assert skipped_gap == 0

        events, skipped_insufficient, skipped_gap = find_month_turn_events(
            [_daily_bar("2026-01-30", 100.0)], hold_days=1
        )
        assert events == []


class TestComputeSummary:
    def test_computes_expected_statistics(self) -> None:
        events = [
            MonthTurnEvent(datetime(2026, 1, 30), datetime(2026, 2, 1), 100.0, 102.0, 2.0),
            MonthTurnEvent(datetime(2026, 2, 26), datetime(2026, 3, 1), 100.0, 99.0, -1.0),
            MonthTurnEvent(datetime(2026, 3, 30), datetime(2026, 4, 1), 100.0, 103.0, 3.0),
            MonthTurnEvent(datetime(2026, 4, 29), datetime(2026, 5, 1), 100.0, 100.0, 0.0),
        ]

        summary = compute_summary(events, hold_days=1, skipped_insufficient_data=0, skipped_large_gap=0)

        assert summary.n_events == 4
        assert summary.mean_return_pct == pytest.approx(1.0)
        assert summary.median_return_pct == pytest.approx(1.0)
        assert summary.stdev_return_pct == pytest.approx(1.825741858, rel=1e-6)
        assert summary.t_statistic == pytest.approx(1.095445115, rel=1e-6)
        assert summary.degrees_of_freedom == 3
        assert summary.positive_count == 2
        assert summary.negative_count == 1
        assert summary.zero_count == 1

    def test_handles_zero_and_one_event_without_crashing(self) -> None:
        summary_zero = compute_summary([], hold_days=1, skipped_insufficient_data=0, skipped_large_gap=0)
        assert summary_zero.n_events == 0
        assert summary_zero.stdev_return_pct is None
        assert summary_zero.t_statistic is None
        assert summary_zero.degrees_of_freedom == 0

        one_event = [MonthTurnEvent(datetime(2026, 1, 30), datetime(2026, 2, 1), 100.0, 102.0, 2.0)]
        summary_one = compute_summary(one_event, hold_days=1, skipped_insufficient_data=0, skipped_large_gap=0)
        assert summary_one.n_events == 1
        assert summary_one.stdev_return_pct is None  # sample stdev undefined for n=1
        assert summary_one.t_statistic is None
        assert summary_one.degrees_of_freedom == 0

    def test_constant_returns_give_none_t_statistic_not_infinite(self) -> None:
        """All-identical nonzero returns -> stdev is 0, so t-statistic must be None
        (not a division-by-zero crash or an infinite value).
        """
        events = [
            MonthTurnEvent(datetime(2026, 1, 30), datetime(2026, 2, 1), 100.0, 102.0, 2.0),
            MonthTurnEvent(datetime(2026, 2, 26), datetime(2026, 3, 1), 100.0, 102.0, 2.0),
        ]
        summary = compute_summary(events, hold_days=1, skipped_insufficient_data=0, skipped_large_gap=0)
        assert summary.stdev_return_pct == pytest.approx(0.0)
        assert summary.t_statistic is None


class TestRunStudy:
    def test_runs_multiple_hold_days_independently(self, two_month_turn_bars: list[Bar]) -> None:
        results = run_study(two_month_turn_bars, hold_days_list=[1, 3, 5])

        assert set(results.keys()) == {1, 3, 5}
        events_1, summary_1 = results[1]
        events_3, summary_3 = results[3]
        events_5, summary_5 = results[5]

        assert summary_1.n_events == 2
        assert summary_3.n_events == 2
        assert summary_5.n_events == 1  # one event skipped for insufficient forward data
        assert summary_5.skipped_insufficient_data == 1

        # Different hold_days must yield different exit dates for the same event.
        assert events_1[0].day_plus_n_date != events_3[0].day_plus_n_date
