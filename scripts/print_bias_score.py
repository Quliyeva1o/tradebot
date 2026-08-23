#!/usr/bin/env python3
"""Read-only CLI: prints a directional BiasScore for the most recently closed bar.

SAFETY -- READ-ONLY. Same contract as live_signal_check.py: never places or
modifies orders. Decision-support only -- this score is NOT consumed by any
strategy or execution code.

Usage:
    python scripts/print_bias_score.py --source csv --data-file data/history/USTEC_M5.csv --symbol USTEC --timeframe M5
    python scripts/print_bias_score.py --source mt5 --symbol USTEC --timeframe M5
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from analysis.bias_score import BiasScore, BiasScorer
from application.services.market_state_builder import MarketStateBuilder
from core.models import Bar, Timeframe
from data.csv_provider import CSVDataProvider
from mt5.connector import MT5Connector
from research.regime_analysis import analyze_regime
from utils.logging import setup_logger

logger = setup_logger("print_bias_score")

DEFAULT_LOOKBACK_BARS = 1000
DEFAULT_WINDOW_BARS = 200


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parses command-line arguments for the bias-score check."""
    parser = argparse.ArgumentParser(
        description="Read-only directional bias score check (no orders placed)."
    )
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
    parser.add_argument(
        "--window-bars",
        type=int,
        default=DEFAULT_WINDOW_BARS,
        help=f"Most-recent bars used for the regime factor (default: {DEFAULT_WINDOW_BARS}).",
    )
    parser.add_argument(
        "--no-regime", action="store_true", help="Skip the optional regime factor entirely."
    )
    return parser.parse_args(argv)


def load_bars(args: argparse.Namespace) -> list[Bar]:
    """Loads bars from the configured source (CSV file or live MT5 fetch)."""
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


def format_bias_score(score: BiasScore) -> str:
    """Formats a BiasScore as a JSON string for machine/human-readable output."""
    return json.dumps(
        {
            "symbol": score.symbol,
            "timeframe": score.timeframe.value,
            "direction": score.direction.name if score.direction is not None else None,
            "probability": round(score.probability, 4),
            "confidence": round(score.confidence, 4),
            "timestamp": score.timestamp.isoformat(),
            "factors": [
                {
                    "name": f.name,
                    "direction": f.direction.name if f.direction is not None else None,
                    "weight": f.weight,
                    "contribution": round(f.contribution, 4),
                }
                for f in score.factors
            ],
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: loads bars, builds MarketState, prints a BiasScore."""
    args = parse_args(argv)
    timeframe = Timeframe[args.timeframe]

    bars = load_bars(args)
    logger.info("Loaded %d bar(s) for %s [%s].", len(bars), args.symbol, args.timeframe)

    builder = MarketStateBuilder(symbol=args.symbol, timeframe=timeframe)
    builder.initialize(bars)

    regime = (
        None
        if args.no_regime
        else analyze_regime(bars, symbol=args.symbol, timeframe=timeframe, window_bars=args.window_bars)
    )

    score = BiasScorer().score(builder.market_state, regime=regime)
    print(format_bias_score(score))


if __name__ == "__main__":
    main()
