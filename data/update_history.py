"""Incremental update for an existing data/history/{SYMBOL}_{TF}.csv file.

download_history.py's own download_symbol() always fetches and OVERWRITES
the full [start, end] range -- fine for a first download, wasteful (and
slow: millions of M1 bars) for "just bring an existing multi-year file up
to today." This reads the existing CSV, fetches only a short OVERLAPPING
recent window from MT5 (overlap so a partial/gappy tail from a previous
run is repaired, not just extended), merges, and re-runs the same
dedup/sort/OHLC-validation pipeline download_history.py already has before
rewriting the file.

Usage:
    python -m data.update_history --symbols XAUUSD,NAS100 --timeframe M1 --overlap-days 5
"""

from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.models import Bar
from data.download_history import (
    DEFAULT_OUTPUT_DIR,
    _print_summary,
    fetch_symbol_bars_chunked,
    validate_bars,
    write_bars_csv,
)
from mt5.connector import MT5Connector
from mt5.rates import BROKER_TZ
from utils.logging import setup_logger

logger = setup_logger("update_history")


def load_existing_bars(path: Path) -> list[Bar]:
    """Reads an existing history CSV (download_history.py's own format:
    naive broker-local "time" column) back into Bar objects."""
    if not path.exists():
        return []
    bars = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            naive = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            ts = naive.replace(tzinfo=BROKER_TZ)
            bars.append(Bar(
                timestamp=ts, open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                volume=float(row["volume"]), spread=float(row.get("spread", 0.0)),
            ))
    return bars


def update_symbol(symbol: str, timeframe: str, overlap_days: int, output_dir: Path) -> None:
    path = output_dir / f"{symbol}_{timeframe}.csv"
    existing = load_existing_bars(path)
    now = datetime.now(UTC)

    if existing:
        fetch_start = max(existing[-1].timestamp, existing[0].timestamp) - timedelta(days=overlap_days)
        # existing may be unsorted on disk in edge cases; use max() over the whole list to be safe
        fetch_start = max(b.timestamp for b in existing) - timedelta(days=overlap_days)
        logger.info("[%s %s] %d existing bars, latest %s -- fetching from %s (overlap) to now.",
                    symbol, timeframe, len(existing), max(b.timestamp for b in existing), fetch_start)
    else:
        raise RuntimeError(f"No existing file at {path} -- use data.download_history for a first full download.")

    new_bars = fetch_symbol_bars_chunked(symbol, timeframe, fetch_start, now)
    logger.info("[%s %s] Fetched %d bar(s) in the update window.", symbol, timeframe, len(new_bars))

    combined = existing + new_bars
    validated, report = validate_bars(combined, symbol, timeframe)
    write_bars_csv(validated, symbol, timeframe, output_dir)
    logger.info("[%s %s] Wrote %d bars (was %d, +%d net new) to %s",
                symbol, timeframe, len(validated), len(existing), len(validated) - len(existing), path)
    _print_summary([report])


def main() -> None:
    parser = argparse.ArgumentParser(description="Incrementally update existing MT5 historical OHLCV CSVs.")
    parser.add_argument("--symbols", required=True, help="Comma-separated symbol list, e.g. XAUUSD,NAS100")
    parser.add_argument("--timeframe", default="M1")
    parser.add_argument("--overlap-days", type=int, default=5, help="Re-fetch this many days before the existing file's last bar, to repair any gap.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    output_dir = Path(args.output_dir)

    connector = MT5Connector()
    if not connector.connect():
        logger.error("Aborting: could not connect to MT5 terminal.")
        raise SystemExit(1)
    try:
        for symbol in symbols:
            update_symbol(symbol, args.timeframe, args.overlap_days, output_dir)
    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()
