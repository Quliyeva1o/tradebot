#!/usr/bin/env python3
"""CLI: runs research.regime_analysis.analyze_regime and exports a markdown report.

Usage:
    python scripts/run_regime_study.py --data-file data/history/USTEC_M5.csv --symbol USTEC --timeframe M5
    python scripts/run_regime_study.py --source mt5 --symbol USTEC --timeframe M5 --window-bars 200
"""

import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from core.models import Bar, Timeframe
from data.csv_provider import CSVDataProvider
from mt5.connector import MT5Connector
from research.regime_analysis import RegimeSummary, analyze_regime
from utils.logging import setup_logger
from utils.paths import get_artifacts_dir

logger = setup_logger("run_regime_study")

DEFAULT_WINDOW_BARS = 200
DEFAULT_LOOKBACK_BARS = 1000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parses command-line arguments for the regime study."""
    parser = argparse.ArgumentParser(
        description="Statistical market-regime analysis (trending vs mean-reverting/ranging)."
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
        help=f"Most-recent bars to analyze (default: {DEFAULT_WINDOW_BARS}).",
    )
    return parser.parse_args(argv)


def load_bars(args: argparse.Namespace) -> list[Bar]:
    """Loads bars from the configured source (CSV file or live MT5 fetch)."""
    if args.source == "csv":
        if not args.data_file:
            raise ValueError("--data-file is required when --source csv")
        return CSVDataProvider(args.data_file).load()

    connector = MT5Connector()
    if not connector.connect():
        raise RuntimeError("Could not connect to the MT5 terminal.")
    try:
        return connector.fetch_recent_bars(args.symbol, args.timeframe, args.lookback_bars)
    finally:
        connector.disconnect()


def _format_summary_text(summary: RegimeSummary) -> str:
    return (
        f"{summary.symbol} [{summary.timeframe.value}] ({summary.window_bars} bars): "
        f"regime={summary.regime.value} autocorr(lag1)={summary.autocorrelation_lag1:+.4f} | "
        f"volatility={summary.volatility.bucket} (atr={summary.volatility.atr:.5f}, "
        f"percentile={summary.volatility.atr_percentile:.1f}) | "
        f"mean_move={summary.moves.mean_move:+.5f} median_move={summary.moves.median_move:+.5f} "
        f"stdev_move={summary.moves.stdev_move:.5f} | "
        f"up_bars={summary.moves.up_bar_pct:.1f}% down_bars={summary.moves.down_bar_pct:.1f}%"
    )


def _export_report(summary: RegimeSummary) -> Path:
    """Writes artifacts/regime_summary.md, following turn_of_month_study.py's report convention."""
    artifacts_dir = get_artifacts_dir()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report_path = artifacts_dir / "regime_summary.md"

    md = f"# Market Regime Summary -- {summary.symbol} [{summary.timeframe.value}]\n\n"
    md += (
        "Statistical regime classification from lag-1 autocorrelation of "
        "bar-to-bar close returns: TRENDING (autocorr >= 0.1), MEAN_REVERTING "
        "(autocorr <= -0.1), otherwise RANGING. Decision-support only -- not "
        "wired into any live strategy.\n\n"
    )
    md += "| Metric | Value |\n| --- | ---: |\n"
    md += f"| Window (bars) | {summary.window_bars} |\n"
    md += f"| Regime | {summary.regime.value} |\n"
    md += f"| Autocorrelation (lag 1) | {summary.autocorrelation_lag1:+.4f} |\n"
    md += f"| Volatility bucket | {summary.volatility.bucket} |\n"
    md += f"| ATR | {summary.volatility.atr:.5f} |\n"
    md += f"| ATR percentile | {summary.volatility.atr_percentile:.1f} |\n"
    md += f"| Mean move | {summary.moves.mean_move:+.5f} |\n"
    md += f"| Median move | {summary.moves.median_move:+.5f} |\n"
    md += f"| Stdev move | {summary.moves.stdev_move:.5f} |\n"
    md += f"| Up bars | {summary.moves.up_bar_pct:.1f}% |\n"
    md += f"| Down bars | {summary.moves.down_bar_pct:.1f}% |\n"

    report_path.write_text(md)
    logger.info("Saved regime summary report to %s", report_path)
    return report_path


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: loads bars, runs analyze_regime(), prints and exports results."""
    args = parse_args(argv)
    timeframe = Timeframe[args.timeframe]

    bars = load_bars(args)
    logger.info("Loaded %d bar(s) for %s [%s].", len(bars), args.symbol, args.timeframe)

    summary = analyze_regime(bars, symbol=args.symbol, timeframe=timeframe, window_bars=args.window_bars)
    print(_format_summary_text(summary))
    _export_report(summary)


if __name__ == "__main__":
    main()
