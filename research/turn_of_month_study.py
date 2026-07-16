#!/usr/bin/env python3
"""Turn-of-Month seasonality event study.

This is a pure statistical/historical analysis tool, NOT a TradeSetupStrategy.
It answers "does this documented effect exist in our own data?" before any
live-tradeable version is built. Because it analyzes the full bar series at
once (not bar-by-bar during a live-style simulation), it is free to look at
bar[i+1] onward when classifying bar[i] as the last trading day of a month --
that is exactly what a real-time strategy CANNOT safely do (see
strategy/turn_of_month.py's future design notes), which is why this event
study and any eventual live strategy will necessarily measure different
(closely related but not identical) return windows.

Event definition, for a given hold_days=N:
    - "day -1": the last trading day of month M (bar[i], where bar[i+1] falls
      in month M+1).
    - "day +N": N trading days after day -1 (bar[i + N]).
    - Event return = pct change from day -1's close to day +N's close.

Usage:
    python research/turn_of_month_study.py --data-file data/history/USTEC_D1.csv --hold-days 1,3,5
"""

import argparse
import csv
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.models import Bar
from data.csv_provider import CSVDataProvider
from utils.logging import setup_logger
from utils.paths import get_artifacts_dir

logger = setup_logger("turn_of_month_study")

# A gap between "day -1" and the next bar larger than this is treated as a data
# irregularity (e.g. a missing month of history), not a genuine month turn, and
# the event is skipped rather than silently misattributed.
DEFAULT_MAX_GAP_DAYS = 10


@dataclass(frozen=True)
class MonthTurnEvent:
    """One historical month-turn observation: entry at day -1's close, exit at day +N's close."""

    day_minus_1_date: datetime
    day_plus_n_date: datetime
    entry_close: float
    exit_close: float
    return_pct: float


@dataclass(frozen=True)
class StudySummary:
    """Statistical summary of all month-turn events for one hold_days value."""

    hold_days: int
    n_events: int
    skipped_insufficient_data: int
    skipped_large_gap: int
    mean_return_pct: float
    median_return_pct: float
    stdev_return_pct: float | None
    t_statistic: float | None
    degrees_of_freedom: int
    positive_count: int
    negative_count: int
    zero_count: int


def find_month_turn_events(
    bars: list[Bar], hold_days: int, max_gap_days: int = DEFAULT_MAX_GAP_DAYS
) -> tuple[list[MonthTurnEvent], int, int]:
    """Finds every "day -1 -> day +N" month-turn event in a chronological bar series.

    Args:
        bars: Chronologically sorted, validated D1 bars.
        hold_days: N -- how many trading days after day -1 to measure the return to.
        max_gap_days: A calendar-day gap between day -1 and the very next bar wider
            than this is treated as a data irregularity (e.g. a missing month),
            not a genuine month turn, and the event is skipped.

    Returns:
        A tuple of (events, skipped_insufficient_data, skipped_large_gap).
        skipped_insufficient_data counts month-turns found too close to the end
        of the series to have hold_days of forward data. skipped_large_gap
        counts month-turns discarded for exceeding max_gap_days.
    """
    events: list[MonthTurnEvent] = []
    skipped_insufficient_data = 0
    skipped_large_gap = 0

    for i in range(len(bars) - 1):
        current_month = (bars[i].timestamp.year, bars[i].timestamp.month)
        next_month = (bars[i + 1].timestamp.year, bars[i + 1].timestamp.month)
        if current_month == next_month:
            continue

        gap_days = (bars[i + 1].timestamp - bars[i].timestamp).days
        if gap_days > max_gap_days:
            skipped_large_gap += 1
            continue

        exit_index = i + hold_days
        if exit_index >= len(bars):
            skipped_insufficient_data += 1
            continue

        entry_bar = bars[i]
        exit_bar = bars[exit_index]
        return_pct = (exit_bar.close - entry_bar.close) / entry_bar.close * 100.0
        events.append(
            MonthTurnEvent(
                day_minus_1_date=entry_bar.timestamp,
                day_plus_n_date=exit_bar.timestamp,
                entry_close=entry_bar.close,
                exit_close=exit_bar.close,
                return_pct=return_pct,
            )
        )

    return events, skipped_insufficient_data, skipped_large_gap


def compute_summary(
    events: list[MonthTurnEvent],
    hold_days: int,
    skipped_insufficient_data: int,
    skipped_large_gap: int,
) -> StudySummary:
    """Computes descriptive statistics and a one-sample t-statistic (vs. 0) over event returns.

    Note: this reports the t-statistic and degrees of freedom, not a p-value --
    computing an exact p-value needs a t-distribution CDF (scipy.stats.t), and
    scipy is not a dependency of this project. For reference, common two-tailed
    critical values at df > ~30 are approximately |t| > 1.96 (5%) and |t| > 2.58 (1%).
    """
    n = len(events)
    returns = [e.return_pct for e in events]

    mean_return = statistics.fmean(returns) if returns else 0.0
    median_return = statistics.median(returns) if returns else 0.0
    stdev_return = statistics.stdev(returns) if n >= 2 else None

    t_statistic: float | None = None
    if stdev_return is not None and stdev_return > 0:
        standard_error = stdev_return / (n**0.5)
        t_statistic = mean_return / standard_error

    positive_count = sum(1 for r in returns if r > 0)
    negative_count = sum(1 for r in returns if r < 0)
    zero_count = sum(1 for r in returns if r == 0)

    return StudySummary(
        hold_days=hold_days,
        n_events=n,
        skipped_insufficient_data=skipped_insufficient_data,
        skipped_large_gap=skipped_large_gap,
        mean_return_pct=mean_return,
        median_return_pct=median_return,
        stdev_return_pct=stdev_return,
        t_statistic=t_statistic,
        degrees_of_freedom=max(0, n - 1),
        positive_count=positive_count,
        negative_count=negative_count,
        zero_count=zero_count,
    )


def run_study(
    bars: list[Bar], hold_days_list: list[int], max_gap_days: int = DEFAULT_MAX_GAP_DAYS
) -> dict[int, tuple[list[MonthTurnEvent], StudySummary]]:
    """Runs the event study for every hold_days value requested.

    Returns:
        A dict mapping hold_days -> (events, summary).
    """
    results: dict[int, tuple[list[MonthTurnEvent], StudySummary]] = {}
    for hold_days in hold_days_list:
        events, skipped_insufficient, skipped_gap = find_month_turn_events(
            bars, hold_days, max_gap_days
        )
        summary = compute_summary(events, hold_days, skipped_insufficient, skipped_gap)
        results[hold_days] = (events, summary)
    return results


def _format_summary_text(summary: StudySummary) -> str:
    stdev_str = f"{summary.stdev_return_pct:.4f}%" if summary.stdev_return_pct is not None else "N/A"
    t_str = f"{summary.t_statistic:+.4f}" if summary.t_statistic is not None else "N/A"
    return (
        f"hold_days={summary.hold_days}: n={summary.n_events} events "
        f"(skipped: {summary.skipped_insufficient_data} insufficient data, "
        f"{summary.skipped_large_gap} large gap) | "
        f"mean={summary.mean_return_pct:+.4f}% median={summary.median_return_pct:+.4f}% "
        f"stdev={stdev_str} | t-stat={t_str} (df={summary.degrees_of_freedom}) | "
        f"positive={summary.positive_count} negative={summary.negative_count} zero={summary.zero_count}"
    )


def _export_artifacts(
    symbol: str, results: dict[int, tuple[list[MonthTurnEvent], StudySummary]]
) -> None:
    """Exports turn_of_month_events.csv (every individual event) and
    turn_of_month_report.md (statistical summary per hold_days).
    """
    artifacts_dir = get_artifacts_dir()
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # 1. Every individual event, across all hold_days values tested.
    events_path = artifacts_dir / "turn_of_month_events.csv"
    with open(events_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["hold_days", "day_minus_1", "day_plus_n", "entry_close", "exit_close", "return_pct"])
        for hold_days, (events, _summary) in results.items():
            for e in events:
                writer.writerow(
                    [
                        hold_days,
                        e.day_minus_1_date.strftime("%Y-%m-%d"),
                        e.day_plus_n_date.strftime("%Y-%m-%d"),
                        f"{e.entry_close:.2f}",
                        f"{e.exit_close:.2f}",
                        f"{e.return_pct:.4f}",
                    ]
                )
    logger.info("Saved turn-of-month events CSV to %s", events_path)

    # 2. Statistical summary report.
    report_path = artifacts_dir / "turn_of_month_report.md"
    md = f"# Turn-of-Month Seasonality Event Study -- {symbol}\n\n"
    md += (
        "Event definition: entry at the close of the last trading day of a month "
        '("day -1"), exit at the close of N trading days into the following month '
        '("day +N"). This is a historical event study, not a live-tradeable '
        "simulation -- it is not subject to the look-ahead constraints a bar-by-bar "
        "strategy must respect.\n\n"
    )
    md += (
        "| Hold Days (N) | Events | Mean Return | Median Return | Stdev | t-statistic | df | "
        "Positive | Negative | Zero |\n"
    )
    md += "| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |\n"
    for hold_days in sorted(results.keys()):
        _events, s = results[hold_days]
        stdev_str = f"{s.stdev_return_pct:.4f}%" if s.stdev_return_pct is not None else "N/A"
        t_str = f"{s.t_statistic:+.4f}" if s.t_statistic is not None else "N/A"
        md += (
            f"| {s.hold_days} | {s.n_events} | {s.mean_return_pct:+.4f}% | "
            f"{s.median_return_pct:+.4f}% | {stdev_str} | {t_str} | {s.degrees_of_freedom} | "
            f"{s.positive_count} | {s.negative_count} | {s.zero_count} |\n"
        )
    md += (
        "\n_t-statistic is a one-sample test against a mean of 0. Exact p-values require "
        "a t-distribution CDF (scipy is not a project dependency); as a rough guide, "
        "for df > ~30 a two-tailed test is approximately significant at the 5% level "
        "when |t| > 1.96, and at the 1% level when |t| > 2.58._\n"
    )
    with open(report_path, "w") as f:
        f.write(md)
    logger.info("Saved turn-of-month report MD to %s", report_path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parses command-line arguments for the study run."""
    parser = argparse.ArgumentParser(description="Turn-of-Month seasonality event study.")
    parser.add_argument("--data-file", required=True, help="Path to a D1 CSV bar file.")
    parser.add_argument(
        "--hold-days",
        default="1,3,5",
        help="Comma-separated list of N (trading days held past day -1) to test (default: 1,3,5).",
    )
    parser.add_argument(
        "--max-gap-days",
        type=int,
        default=DEFAULT_MAX_GAP_DAYS,
        help="Discard a month-turn event if the calendar gap to the next bar exceeds this many days.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: loads a D1 CSV, runs the study, prints and exports results."""
    args = parse_args(argv)
    hold_days_list = [int(x.strip()) for x in args.hold_days.split(",") if x.strip()]

    provider = CSVDataProvider(filepath=args.data_file)
    bars = provider.load()
    provider.validate(bars)

    symbol = Path(args.data_file).stem
    logger.info(
        "Loaded %d bars for %s (%s -> %s)",
        len(bars),
        symbol,
        bars[0].timestamp if bars else "N/A",
        bars[-1].timestamp if bars else "N/A",
    )

    results = run_study(bars, hold_days_list, args.max_gap_days)

    print(f"\n=== Turn-of-Month Seasonality Study: {symbol} ===")
    print(f"Data range: {bars[0].timestamp.date()} -> {bars[-1].timestamp.date()} ({len(bars)} bars)\n")
    for hold_days in sorted(results.keys()):
        _events, summary = results[hold_days]
        print(_format_summary_text(summary))
    print()

    _export_artifacts(symbol, results)


if __name__ == "__main__":
    main()
