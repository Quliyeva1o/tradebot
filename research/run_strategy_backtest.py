#!/usr/bin/env python3
"""Reproducible single-strategy backtest runner.

Every strategy trial (manual research, CI regression, walk-forward sanity
check) should go through this script so runs are reproducible from a single
command line: same CSV, same params, same split -> same JSON result.

Usage:
    python research/run_strategy_backtest.py \\
        --strategy order_block_retest \\
        --data-file data/history/EURUSD_M15.csv \\
        --timeframe M15 \\
        --params '{"risk_reward": 3.0}' \\
        --split in_sample --split-ratio 0.7
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Add project root to python path so the script also works when invoked
# directly (python research/run_strategy_backtest.py) rather than as a module.
sys.path.append(str(Path(__file__).parent.parent.resolve()))

from application.services.market_state_builder import MarketStateBuilder
from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from backtest.report import BacktestReportGenerator
from core.models import Bar, Timeframe
from data.csv_provider import CSVDataProvider
from strategy.accumulation_breakout import AccumulationBreakoutStrategy
from strategy.continuation import BearishContinuationStrategy, BullishContinuationStrategy
from strategy.interfaces import TradeSetupStrategy
from strategy.nasdaq_midline_sweep import NasdaqMidlineSweepStrategy
from strategy.opening_range_breakout import OpeningRangeBreakoutStrategy
from strategy.order_block_retest import OrderBlockRetestStrategy
from strategy.strategy_engine import StrategyEngine

STRATEGY_REGISTRY: dict[str, type[TradeSetupStrategy]] = {
    "accumulation_breakout": AccumulationBreakoutStrategy,
    "midline_sweep": NasdaqMidlineSweepStrategy,
    "opening_range_breakout": OpeningRangeBreakoutStrategy,
    "order_block_retest": OrderBlockRetestStrategy,
    "continuation_bullish": BullishContinuationStrategy,
    "continuation_bearish": BearishContinuationStrategy,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parses command-line arguments for the backtest run."""
    parser = argparse.ArgumentParser(
        description="Run a single strategy backtest against a CSV bar file."
    )
    parser.add_argument(
        "--strategy",
        required=True,
        choices=sorted(STRATEGY_REGISTRY.keys()),
        help="Strategy to backtest.",
    )
    parser.add_argument("--data-file", required=True, help="Path to the CSV bar file.")
    parser.add_argument(
        "--timeframe",
        required=True,
        help="Bar timeframe, e.g. M1, M5, M15, M30, H1, H4, D1.",
    )
    parser.add_argument(
        "--params",
        default="{}",
        help="JSON string of strategy constructor kwargs, e.g. '{\"risk_reward\": 3.0}'.",
    )
    parser.add_argument(
        "--split",
        default="full",
        choices=["full", "in_sample", "out_of_sample"],
        help="Which chronological slice of the bars to backtest (default: full).",
    )
    parser.add_argument(
        "--split-ratio",
        type=float,
        default=0.7,
        help="Fraction of bars used as in-sample when --split != full (default: 0.7).",
    )
    parser.add_argument(
        "--initial-balance",
        type=float,
        default=10_000.0,
        help="Starting account balance (default: 10000.0).",
    )
    parser.add_argument(
        "--spread",
        type=float,
        default=0.0002,
        help="Full bid/ask spread applied to trades (default: 0.0002).",
    )
    parser.add_argument(
        "--commission",
        type=float,
        default=0.0,
        help="Flat commission fee per trade (default: 0.0).",
    )
    parser.add_argument(
        "--risk-per-trade",
        type=float,
        default=0.01,
        help="Fraction of account balance risked per trade (default: 0.01).",
    )
    return parser.parse_args(argv)


def resolve_timeframe(tf_str: str) -> Timeframe:
    """Resolves a CLI timeframe string to a Timeframe enum member."""
    try:
        return Timeframe[tf_str]
    except KeyError as exc:
        valid = ", ".join(t.name for t in Timeframe)
        raise ValueError(f"Unknown timeframe {tf_str!r}. Valid values: {valid}") from exc


def split_bars(bars: list[Bar], split: str, split_ratio: float) -> list[Bar]:
    """Slices bars chronologically for in-sample / out-of-sample evaluation.

    Bar order is preserved (no shuffling): in-sample is always the earlier
    period, out-of-sample the later one, matching time-series semantics.
    """
    if split == "full":
        return bars

    split_index = int(len(bars) * split_ratio)
    if split == "in_sample":
        return bars[:split_index]
    return bars[split_index:]


def build_strategy(strategy_name: str, params: dict[str, Any]) -> TradeSetupStrategy:
    """Constructs the strategy instance named by --strategy with --params kwargs."""
    strategy_cls = STRATEGY_REGISTRY[strategy_name]
    return strategy_cls(**params)


def run_backtest(
    strategy_name: str,
    data_file: str,
    timeframe: Timeframe,
    params: dict[str, Any],
    split: str,
    split_ratio: float,
    initial_balance: float,
    spread: float,
    commission: float,
    risk_per_trade: float,
) -> dict[str, Any]:
    """Loads data, runs the split backtest, and returns a JSON-serializable result dict."""
    provider = CSVDataProvider(filepath=data_file)
    all_bars = provider.load()
    provider.validate(all_bars)

    bars = split_bars(all_bars, split, split_ratio)

    symbol = Path(data_file).stem
    strategy = build_strategy(strategy_name, params)

    state_builder = MarketStateBuilder(symbol=symbol, timeframe=timeframe)
    strategy_engine = StrategyEngine()
    strategy_engine.register_strategy(strategy)

    backtest_config = BacktestConfig(
        initial_balance=initial_balance,
        risk_per_trade=risk_per_trade,
        spread=spread,
        commission=commission,
        slippage=0.0,
    )
    engine = BacktestEngine(config=backtest_config)
    result = engine.run(bars, strategy_engine, state_builder)

    metrics = BacktestReportGenerator().generate(result)

    date_range = (
        [bars[0].timestamp.isoformat(), bars[-1].timestamp.isoformat()] if bars else [None, None]
    )

    return {
        "strategy": strategy_name,
        "symbol": symbol,
        "split": split,
        "split_ratio": split_ratio,
        "bars_used": len(bars),
        "date_range": date_range,
        "total_trades": metrics.total_trades,
        "win_rate": metrics.win_rate,
        "profit_factor": metrics.profit_factor,
        "net_profit": metrics.net_profit,
        "max_drawdown": metrics.max_drawdown,
        "params": params,
    }


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    timeframe = resolve_timeframe(args.timeframe)
    params = json.loads(args.params)

    output = run_backtest(
        strategy_name=args.strategy,
        data_file=args.data_file,
        timeframe=timeframe,
        params=params,
        split=args.split,
        split_ratio=args.split_ratio,
        initial_balance=args.initial_balance,
        spread=args.spread,
        commission=args.commission,
        risk_per_trade=args.risk_per_trade,
    )
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
