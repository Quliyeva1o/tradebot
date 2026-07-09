#!/usr/bin/env python3
"""Structure-trend and strategy-rejection diagnostics runner.

Runs a single MarketStateBuilder pass per symbol (trend distribution and
strategy diagnostics computed together, not as two separate passes) over the
downloaded data/history/{SYMBOL}_{TIMEFRAME}.csv file. No parameters are
changed and no trades are placed -- this only observes structure/strategy
behavior across the requested history.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).parent.resolve()))

from application.services.market_state_builder import MarketStateBuilder
from core.models import Timeframe
from data.csv_provider import CSVDataProvider
from strategy.continuation import BearishContinuationStrategy, BullishContinuationStrategy
from strategy.strategy_engine import StrategyEngine
from utils.logging import setup_logger

logger = setup_logger("run_diagnostics")

DEFAULT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]
DEFAULT_TIMEFRAME = "M15"
DEFAULT_HISTORY_DIR = Path("data/history")


@dataclass
class SymbolDiagnostics:
    """Trend distribution and strategy rejection summary for one symbol."""

    symbol: str
    timeframe: str
    total_bars: int
    trend_counts: dict[str, int] = field(default_factory=dict)
    trend_pct: dict[str, float] = field(default_factory=dict)
    strategy_diagnostics: dict[str, dict] = field(default_factory=dict)


def run_diagnostics_for_symbol(
    symbol: str, timeframe: str = DEFAULT_TIMEFRAME, history_dir: Path = DEFAULT_HISTORY_DIR
) -> SymbolDiagnostics:
    """Computes trend distribution and strategy diagnostics in a single pass.

    Args:
        symbol: Trading instrument symbol (e.g. "EURUSD").
        timeframe: Timeframe key matching the downloaded CSV (e.g. "M15").
        history_dir: Directory containing {symbol}_{timeframe}.csv.

    Returns:
        SymbolDiagnostics with trend distribution and strategy rejection counts.
    """
    csv_path = history_dir / f"{symbol}_{timeframe}.csv"
    bars = CSVDataProvider(filepath=csv_path).load()

    try:
        timeframe_enum = Timeframe[timeframe]
    except KeyError:
        timeframe_enum = Timeframe.M15

    builder = MarketStateBuilder(symbol=symbol, timeframe=timeframe_enum)
    strategy_engine = StrategyEngine()
    strategy_engine.register_strategy(BullishContinuationStrategy(min_risk_reward_ratio=1.0))
    strategy_engine.register_strategy(BearishContinuationStrategy(min_risk_reward_ratio=1.0))

    trend_counts: Counter[str] = Counter()
    for bar in bars:
        state = builder.append_bar(bar)
        trend_counts[state.structure_state.trend.name] += 1
        strategy_engine.run(state)

    total = len(bars)
    trend_pct = {name: round(100 * count / total, 1) for name, count in trend_counts.items()} if total else {}

    return SymbolDiagnostics(
        symbol=symbol,
        timeframe=timeframe,
        total_bars=total,
        trend_counts=dict(trend_counts),
        trend_pct=trend_pct,
        strategy_diagnostics=strategy_engine.get_diagnostics(),
    )


def _print_summary(diag: SymbolDiagnostics) -> None:
    print(f"\n=== {diag.symbol} [{diag.timeframe}] -- {diag.total_bars} bars ===")
    print("Trend distribution:")
    for name, count in sorted(diag.trend_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {name:12s} {count:7d}  ({diag.trend_pct[name]:5.1f}%)")
    print("Strategy diagnostics:")
    print(json.dumps(diag.strategy_diagnostics, indent=2))


def main() -> None:
    """CLI entry point: runs diagnostics for one or more symbols, reporting as each completes."""
    parser = argparse.ArgumentParser(description="Structure-trend and strategy-rejection diagnostics.")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="Comma-separated symbol list.")
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--history-dir", default=str(DEFAULT_HISTORY_DIR))
    parser.add_argument("--output-json", default=None, help="Optional path to append each result as JSON.")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    history_dir = Path(args.history_dir)

    for symbol in symbols:
        logger.info("Running diagnostics for %s...", symbol)
        diag = run_diagnostics_for_symbol(symbol, args.timeframe, history_dir)
        _print_summary(diag)
        if args.output_json:
            out_path = Path(args.output_json)
            existing: list[dict[str, Any]] = []
            if out_path.exists():
                existing = json.loads(out_path.read_text())
            existing.append(asdict(diag))
            out_path.write_text(json.dumps(existing, indent=2))
        logger.info("Finished %s.", symbol)


if __name__ == "__main__":
    main()
