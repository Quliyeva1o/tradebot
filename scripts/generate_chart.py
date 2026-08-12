#!/usr/bin/env python3
"""CLI: renders a candlestick chart with SMC/structure overlays to a PNG file.

READ-ONLY with respect to MT5 when --source mt5 is used (only calls
MT5Connector.fetch_recent_bars(), never places/modifies orders -- same
safety contract as live_signal_check.py).

Usage:
    python scripts/generate_chart.py --source csv --data-file data/history/USTEC_M5.csv --symbol USTEC --timeframe M5
    python scripts/generate_chart.py --source mt5 --symbol USTEC --timeframe M5 --lookback-bars 500
"""

import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from application.services.market_state_builder import MarketStateBuilder
from core.models import Bar, Timeframe
from dashboard.chart_data import build_overlay_data, compute_trend_lines
from dashboard.static_renderer import render_price_chart
from data.csv_provider import CSVDataProvider
from mt5.connector import MT5Connector
from utils.logging import setup_logger
from utils.paths import get_artifacts_dir

logger = setup_logger("generate_chart")

DEFAULT_LOOKBACK_BARS = 500


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parses command-line arguments for chart generation."""
    parser = argparse.ArgumentParser(description="Render a candlestick chart with SMC overlays to PNG.")
    parser.add_argument("--symbol", default="USTEC", help="Trading instrument symbol (default: USTEC).")
    parser.add_argument("--timeframe", default="M5", help="Bar timeframe (default: M5).")
    parser.add_argument(
        "--source", choices=["csv", "mt5"], default="csv", help="Bar data source (default: csv)."
    )
    parser.add_argument("--data-file", help="CSV file path (required when --source csv).")
    parser.add_argument(
        "--lookback-bars",
        type=int,
        default=DEFAULT_LOOKBACK_BARS,
        help=f"Bars to fetch when --source mt5 (default: {DEFAULT_LOOKBACK_BARS}).",
    )
    parser.add_argument("--output", help="Output PNG path (default: artifacts/charts/<symbol>_<timeframe>.png).")
    return parser.parse_args(argv)


def load_bars(args: argparse.Namespace) -> list[Bar]:
    """Loads bars from the configured source (CSV file or live MT5 fetch).

    Both sources are trimmed to the most recent --lookback-bars: a full
    history CSV can hold 100k+ rows, which is unreadable as a single chart
    and far more than MarketStateBuilder/the renderer need to draw a useful
    recent-price picture.
    """
    if args.source == "csv":
        if not args.data_file:
            raise ValueError("--data-file is required when --source csv")
        bars = CSVDataProvider(args.data_file).load()
        return bars[-args.lookback_bars :] if args.lookback_bars > 0 else bars

    connector = MT5Connector()
    if not connector.connect():
        raise RuntimeError("Could not connect to the MT5 terminal.")
    try:
        return connector.fetch_recent_bars(args.symbol, args.timeframe, args.lookback_bars)
    finally:
        connector.disconnect()


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: loads bars, builds MarketState, renders the chart PNG."""
    args = parse_args(argv)
    timeframe = Timeframe[args.timeframe]

    bars = load_bars(args)
    logger.info("Loaded %d bar(s) for %s [%s].", len(bars), args.symbol, args.timeframe)

    builder = MarketStateBuilder(symbol=args.symbol, timeframe=timeframe)
    builder.initialize(bars)

    overlay_data = build_overlay_data(builder.market_state)
    trend_lines = compute_trend_lines(overlay_data.swings)

    output_path = (
        Path(args.output)
        if args.output
        else get_artifacts_dir() / "charts" / f"{args.symbol}_{args.timeframe}.png"
    )
    render_price_chart(
        overlay_data, trend_lines, output_path, title=f"{args.symbol} [{args.timeframe}]"
    )
    logger.info("Chart written to %s", output_path)
    print(f"Chart written to {output_path}")


if __name__ == "__main__":
    main()
