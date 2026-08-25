"""MT5 historical OHLCV downloader with strict no-synthetic-data validation.

Fetches bars via MetaTrader5.copy_rates_range() and writes them to
data/history/{SYMBOL}_{TIMEFRAME}.csv in the format core.data.csv_provider
expects. Validation only ever detects, logs, removes exact duplicates, and
sorts existing rows -- it never fabricates or interpolates missing bars.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import MetaTrader5 as mt5  # noqa: N813

from core.models import Bar, Timeframe
from mt5.chunking import TIMEFRAME_DELTA as _TIMEFRAME_DELTA
from mt5.chunking import iter_chunk_windows as _iter_chunk_windows
from mt5.connector import MT5Connector
from mt5.rates import BROKER_TZ
from mt5.rates import TIMEFRAME_MAPPING as _MT5_TIMEFRAME_MAP
from mt5.rates import get_symbol_point as _get_symbol_point
from mt5.rates import rates_to_bars as _rows_to_bars
from utils.logging import setup_logger
from utils.validators import OHLCViolation, check_ohlc_consistency

logger = setup_logger("download_history")

DEFAULT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
DEFAULT_TIMEFRAME = "M15"
DEFAULT_START = datetime(2020, 1, 1, tzinfo=UTC)
DEFAULT_OUTPUT_DIR = Path("data/history")


@dataclass(frozen=True)
class GapRecord:
    """A detected discontinuity between two consecutive bars. Never filled."""

    previous_timestamp: datetime
    next_timestamp: datetime
    gap_duration: timedelta
    likely_reason: str


@dataclass(frozen=True)
class ValidationReport:
    """Summary of validation actions taken on one symbol/timeframe download."""

    symbol: str
    timeframe: str
    total_bars: int
    duplicates_removed: int
    reordered_count: int
    gaps: list[GapRecord] = field(default_factory=list)
    ohlc_violations: list[OHLCViolation] = field(default_factory=list)


def fetch_symbol_bars(
    symbol: str, timeframe: str, start: datetime, end: datetime
) -> list[Bar]:
    """Fetches raw bars for a symbol from the connected MT5 terminal.

    Args:
        symbol: Trading instrument symbol (e.g. "EURUSD").
        timeframe: One of the supported timeframe keys (e.g. "M15").
        start: Range start (inclusive).
        end: Range end (inclusive).

    Returns:
        Unvalidated list of Bar objects in the order returned by MT5.

    Raises:
        RuntimeError: If the symbol cannot be selected or no rates are returned.
    """
    mt5_tf = _MT5_TIMEFRAME_MAP[timeframe]

    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Symbol {symbol} is not available in the MT5 terminal.")

    point = _get_symbol_point(symbol)

    rates = mt5.copy_rates_range(symbol, mt5_tf, start, end)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No historical rates returned from MT5 for {symbol} {timeframe}.")

    return _rows_to_bars(rates, point)


def fetch_symbol_bars_chunked(
    symbol: str, timeframe: str, start: datetime, end: datetime
) -> list[Bar]:
    """Fetches bars across [start, end] in size-limited chunks and concatenates them.

    MT5 fails a copy_rates_range call outright (returns None) rather than
    truncating when a single request would exceed its internal history
    buffer, so wide ranges must be split. A chunk with no data (for example
    a window predating the broker's available history) is logged and
    skipped; it does not abort the overall download. Any duplicate bar MT5
    returns at chunk boundaries (or a single clipped bar for out-of-range
    chunks) is removed later by remove_duplicate_timestamps -- this function
    only fetches and concatenates.

    Args:
        symbol: Trading instrument symbol (e.g. "EURUSD").
        timeframe: One of the supported timeframe keys (e.g. "M15").
        start: Overall range start (inclusive).
        end: Overall range end (inclusive).

    Returns:
        Concatenated, unvalidated list of Bar objects across all chunks.

    Raises:
        RuntimeError: If the symbol cannot be selected, or no chunk yields any data.
    """
    mt5_tf = _MT5_TIMEFRAME_MAP[timeframe]

    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Symbol {symbol} is not available in the MT5 terminal.")

    point = _get_symbol_point(symbol)

    all_bars: list[Bar] = []
    for chunk_start, chunk_end in _iter_chunk_windows(start, end, timeframe):
        rates = mt5.copy_rates_range(symbol, mt5_tf, chunk_start, chunk_end)
        if rates is None or len(rates) == 0:
            logger.info(
                "[%s %s] No data for chunk %s -> %s (skipped).",
                symbol,
                timeframe,
                chunk_start,
                chunk_end,
            )
            continue
        logger.info(
            "[%s %s] Fetched %d bar(s) for chunk %s -> %s.",
            symbol,
            timeframe,
            len(rates),
            chunk_start,
            chunk_end,
        )
        all_bars.extend(_rows_to_bars(rates, point))

    if not all_bars:
        raise RuntimeError(
            f"No historical rates returned from MT5 for {symbol} {timeframe} in any chunk."
        )

    return all_bars


def remove_duplicate_timestamps(bars: list[Bar]) -> tuple[list[Bar], int]:
    """Removes bars sharing a timestamp with an earlier bar, keeping the last seen.

    Args:
        bars: Raw bar list, any order.

    Returns:
        Tuple of (deduplicated bars in first-seen order, number removed).
    """
    last_by_ts: dict[datetime, Bar] = {}
    order: list[datetime] = []
    removed = 0
    for bar in bars:
        if bar.timestamp in last_by_ts:
            removed += 1
        else:
            order.append(bar.timestamp)
        last_by_ts[bar.timestamp] = bar
    return [last_by_ts[ts] for ts in order], removed


def ensure_chronological_order(bars: list[Bar]) -> tuple[list[Bar], int]:
    """Sorts bars by timestamp if any are out of order. Reordering, not fabrication.

    Args:
        bars: Bar list, assumed already deduplicated.

    Returns:
        Tuple of (chronologically sorted bars, number of bars that were out of order).
    """
    out_of_order = sum(1 for prev, curr in pairwise(bars) if prev.timestamp > curr.timestamp)
    if out_of_order == 0:
        return bars, 0
    return sorted(bars, key=lambda b: b.timestamp), out_of_order


def _classify_gap(previous_timestamp: datetime, diff: timedelta) -> str:
    """Heuristically explains why a gap occurred, for logging only."""
    if previous_timestamp.weekday() == 4 and diff >= timedelta(hours=40):
        return "market_closed_weekend"
    if diff >= timedelta(hours=20):
        return "extended_closure_or_holiday"
    return "unexplained_gap_possible_broker_interruption"


def detect_gaps(bars: list[Bar], timeframe: str) -> list[GapRecord]:
    """Detects and classifies missing-bar gaps. Never fills them.

    Args:
        bars: Chronologically sorted, deduplicated bar list.
        timeframe: One of the supported timeframe keys, used to size the expected interval.

    Returns:
        List of GapRecord for every consecutive pair exceeding the expected interval.
    """
    delta = _TIMEFRAME_DELTA[timeframe]
    gaps: list[GapRecord] = []
    for prev, curr in pairwise(bars):
        diff = curr.timestamp - prev.timestamp
        if diff <= delta:
            continue
        gaps.append(
            GapRecord(
                previous_timestamp=prev.timestamp,
                next_timestamp=curr.timestamp,
                gap_duration=diff,
                likely_reason=_classify_gap(prev.timestamp, diff),
            )
        )
    return gaps


def validate_bars(bars: list[Bar], symbol: str, timeframe: str) -> tuple[list[Bar], ValidationReport]:
    """Runs the full validation pipeline and logs every finding.

    Args:
        bars: Raw bars as returned by fetch_symbol_bars.
        symbol: Symbol being validated, for log context.
        timeframe: Timeframe key, for log context and gap sizing.

    Returns:
        Tuple of (cleaned bar list, ValidationReport summarizing all findings).
    """
    deduped, dup_count = remove_duplicate_timestamps(bars)
    ordered, reorder_count = ensure_chronological_order(deduped)
    gaps = detect_gaps(ordered, timeframe)
    violations = check_ohlc_consistency(ordered)

    if dup_count:
        logger.warning(
            "[%s %s] Removed %d duplicate timestamp bar(s).", symbol, timeframe, dup_count
        )
    if reorder_count:
        logger.warning(
            "[%s %s] %d bar(s) were out of chronological order; sorted by timestamp.",
            symbol,
            timeframe,
            reorder_count,
        )
    for gap in gaps:
        logger.info(
            "[%s %s] Gap detected: %s -> %s (duration=%s, reason=%s). Not filled.",
            symbol,
            timeframe,
            gap.previous_timestamp,
            gap.next_timestamp,
            gap.gap_duration,
            gap.likely_reason,
        )
    for violation in violations:
        logger.error(
            "[%s %s] OHLC consistency violation at %s: %s",
            symbol,
            timeframe,
            violation.timestamp,
            violation.reason,
        )

    report = ValidationReport(
        symbol=symbol,
        timeframe=timeframe,
        total_bars=len(ordered),
        duplicates_removed=dup_count,
        reordered_count=reorder_count,
        gaps=gaps,
        ohlc_violations=violations,
    )
    return ordered, report


def write_bars_csv(bars: list[Bar], symbol: str, timeframe: str, output_dir: Path) -> Path:
    """Writes bars to data/history/{SYMBOL}_{TIMEFRAME}.csv in Bar-compatible format.

    Bar.timestamp is genuine UTC (see mt5/rates.py's rates_to_bars()), but
    every batch script's own load_bars() (e.g. scripts/first_fvg_backtest.py)
    expects the CSV's naive "time" column to be BROKER-LOCAL (Europe/
    Bucharest) wall-clock, matching what MT5 itself reports and what this
    file format has always contained -- so bars are converted back to
    Bucharest here before formatting, keeping that existing convention (and
    every already-downloaded historical CSV) unchanged by the UTC fix.

    Args:
        bars: Validated bar list to persist.
        symbol: Trading instrument symbol, used in the filename.
        timeframe: Timeframe key, used in the filename.
        output_dir: Directory the CSV is written into (created if missing).

    Returns:
        Path to the written CSV file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    file_path = output_dir / f"{symbol}_{timeframe}.csv"

    with file_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["time", "open", "high", "low", "close", "volume", "spread"])
        for bar in bars:
            writer.writerow(
                [
                    bar.timestamp.astimezone(BROKER_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                    bar.spread,
                ]
            )

    return file_path


def download_symbol(
    symbol: str, timeframe: str, start: datetime, end: datetime, output_dir: Path
) -> ValidationReport:
    """Fetches, validates, and persists historical data for a single symbol.

    Args:
        symbol: Trading instrument symbol.
        timeframe: Timeframe key.
        start: Range start (inclusive).
        end: Range end (inclusive).
        output_dir: Directory the CSV is written into.

    Returns:
        The ValidationReport produced during validation.
    """
    raw_bars = fetch_symbol_bars_chunked(symbol, timeframe, start, end)
    validated_bars, report = validate_bars(raw_bars, symbol, timeframe)
    path = write_bars_csv(validated_bars, symbol, timeframe, output_dir)
    logger.info("[%s %s] Wrote %d bars to %s", symbol, timeframe, len(validated_bars), path)
    return report


def _print_summary(reports: list[ValidationReport]) -> None:
    print("\n=== Download Summary ===")
    for r in reports:
        print(
            f"{r.symbol} [{r.timeframe}]: {r.total_bars} bars written | "
            f"{r.duplicates_removed} duplicates removed | "
            f"{r.reordered_count} reordered | "
            f"{len(r.gaps)} gaps logged | "
            f"{len(r.ohlc_violations)} OHLC violations"
        )
    print("========================\n")


def main() -> None:
    """CLI entry point: downloads and validates historical data for configured symbols."""
    parser = argparse.ArgumentParser(
        description="Download and validate MT5 historical OHLCV data."
    )
    parser.add_argument(
        "--symbols", default=",".join(DEFAULT_SYMBOLS), help="Comma-separated symbol list."
    )
    parser.add_argument(
        "--timeframe",
        default=DEFAULT_TIMEFRAME,
        choices=sorted(tf.value for tf in Timeframe),
    )
    parser.add_argument("--start", default=DEFAULT_START.strftime("%Y-%m-%d"))
    parser.add_argument("--end", default=datetime.now(UTC).strftime("%Y-%m-%d"))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=UTC)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=UTC)
    output_dir = Path(args.output_dir)

    connector = MT5Connector()
    if not connector.connect():
        logger.error("Aborting: could not connect to MT5 terminal.")
        raise SystemExit(1)

    reports: list[ValidationReport] = []
    try:
        for symbol in symbols:
            try:
                reports.append(
                    download_symbol(symbol, args.timeframe, start, end, output_dir)
                )
            except RuntimeError:
                logger.exception("Failed to download %s", symbol)
    finally:
        connector.disconnect()

    _print_summary(reports)


if __name__ == "__main__":
    main()
