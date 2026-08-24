#!/usr/bin/env python3
"""Backtest runner for PineOrderBlockWicksStrategy (the 'ICT MTF Order Block
Wicks [MK]' Pine indicator's own 2-candle OB detection, ported to a
tradeable strategy -- see strategy/pine_order_block_wicks.py).

Deliberately separate from run_backtest.py (which hardcodes the
Bullish/BearishContinuationStrategy pair and writes to artifacts/) so this
run's outputs don't clobber that regression baseline. Writes to
artifacts/pine_ob_backtest/ instead.

Data note: data/history/*.csv spread columns are raw MT5 integer points
(mt5/history_downloader.py writes mt5.copy_rates_range()'s `spread` field
unscaled), NOT the price-space value BacktestEngine.config.spread/
candle.spread expects (mt5/rates.py's rates_to_bars() is the only place
that does the points*point_size conversion, and it isn't used by the CSV
export path). Left uncorrected, every bar's spread cost would be
overstated by ~1/point_size (100x for XAUUSD's 0.01 point size) -- this
script corrects that locally by rescaling before handing bars to the
engine, without touching the shared CSV loading pipeline other callers
still rely on as-is.
"""

import argparse
import csv
import sys
from dataclasses import replace
from pathlib import Path

sys.path.append(str(Path(__file__).parent.resolve()))

from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from backtest.report import BacktestReportGenerator
from core.models import Bar, Timeframe
from data.csv_provider import CSVDataProvider
from market_structure.structure_models import MarketState
from strategy.pine_order_block_wicks import PineOrderBlockWicksStrategy
from strategy.strategy_engine import StrategyEngine
from utils.logging import setup_logger

logger = setup_logger("run_pine_ob_backtest")

XAUUSD_POINT_SIZE = 0.01  # 2-decimal quoting confirmed by the CSV data itself


class _FastMarketStateBuilder:
    """Drop-in for MarketStateBuilder that skips swing/structure/SMC pipeline work.

    PineOrderBlockWicksStrategy only ever reads market_state.bars_view() (the
    last two closed bars) -- it never touches swing_graph, structure_state or
    smc_state. Running the full MarketStateBuilder pipeline (SwingDetector +
    MarketStructureEngine + SMCPipeline, including the liquidity detector the
    project's own technical audit flags as O(n^2 log n) and "unusable past a
    few thousand bars") for every one of ~100k M15 bars would cost several
    minutes of pure waste computing state this strategy never reads. This
    stub keeps BacktestEngine.run()'s exact fill/exit/PnL mechanics (it only
    needs an object exposing initialize()/append_bar() -> MarketState)
    while making state updates O(1) per bar.
    """

    def __init__(self, symbol: str, timeframe: Timeframe) -> None:
        self._market_state = MarketState(symbol=symbol, timeframe=timeframe)

    def initialize(self, history: list[Bar]) -> None:
        self._market_state._bars.clear()
        for bar in history:
            self._market_state.append_bar(bar)

    def append_bar(self, bar: Bar) -> MarketState:
        self._market_state.append_bar(bar)
        return self._market_state

    @property
    def market_state(self) -> MarketState:
        return self._market_state


def main() -> None:
    parser = argparse.ArgumentParser(description="PineOrderBlockWicksStrategy backtest runner")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--start-date", default=None, help="YYYY-MM-DD, inclusive. Default: earliest available.")
    parser.add_argument("--end-date", default=None, help="YYYY-MM-DD, inclusive. Default: latest available.")
    parser.add_argument("--initial-balance", type=float, default=10000.0)
    parser.add_argument("--risk-per-trade", type=float, default=0.01)
    parser.add_argument("--risk-reward", type=float, default=3.0)
    parser.add_argument("--max-active-obs", type=int, default=8)
    parser.add_argument("--min-risk-distance", type=float, default=0.0)
    parser.add_argument("--commission", type=float, default=5.0)
    parser.add_argument("--slippage", type=float, default=0.0)
    parser.add_argument("--point-size", type=float, default=XAUUSD_POINT_SIZE)
    args = parser.parse_args()

    csv_path = Path(f"data/history/{args.symbol}_{args.timeframe}.csv")
    logger.info("Loading %s", csv_path)
    provider = CSVDataProvider(filepath=csv_path)
    bars = provider.load()
    provider.validate(bars)
    logger.info("Loaded %d raw bars", len(bars))

    if args.start_date:
        start_dt = pd_to_aware(bars, args.start_date)
        bars = [b for b in bars if b.timestamp >= start_dt]
    if args.end_date:
        end_dt = pd_to_aware(bars, args.end_date, end_of_day=True)
        bars = [b for b in bars if b.timestamp <= end_dt]

    logger.info(
        "Backtest window: %s .. %s (%d bars)",
        bars[0].timestamp if bars else None,
        bars[-1].timestamp if bars else None,
        len(bars),
    )

    # Rescale raw-points spread -> price-space (see module docstring).
    bars = [replace(b, spread=b.spread * args.point_size) for b in bars]

    timeframe_enum = Timeframe[args.timeframe]
    state_builder = _FastMarketStateBuilder(symbol=args.symbol, timeframe=timeframe_enum)

    strategy = PineOrderBlockWicksStrategy(
        risk_reward=args.risk_reward,
        max_active_obs=args.max_active_obs,
        min_risk_distance=args.min_risk_distance,
    )
    strategy_engine = StrategyEngine()
    strategy_engine.register_strategy(strategy)

    backtest_config = BacktestConfig(
        initial_balance=args.initial_balance,
        risk_per_trade=args.risk_per_trade,
        spread=0.0,  # per-bar CSV spread (rescaled above) takes priority when > 0
        commission=args.commission,
        slippage=args.slippage,
    )
    engine = BacktestEngine(config=backtest_config)

    logger.info("Running backtest...")
    result = engine.run(bars, strategy_engine, state_builder)

    diagnostics = strategy_engine.get_diagnostics()
    logger.info("Strategy diagnostics: %s", diagnostics)

    metrics = BacktestReportGenerator().generate(result)

    artifacts_dir = Path("artifacts/pine_ob_backtest")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    trades_path = artifacts_dir / "trades.csv"
    with open(trades_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "entry_date",
                "entry_time",
                "exit_date",
                "exit_time",
                "direction",
                "entry_price",
                "exit_price",
                "stop_loss",
                "take_profit",
                "result",
                "pnl",
                "r_multiple",
                "bars_held",
                "trigger_reason",
            ]
        )
        for t in result.trades:
            writer.writerow(
                [
                    t.entry_time.strftime("%Y-%m-%d"),
                    t.entry_time.strftime("%H:%M:%S"),
                    t.exit_time.strftime("%Y-%m-%d"),
                    t.exit_time.strftime("%H:%M:%S"),
                    t.direction.name,
                    f"{t.entry_price:.2f}",
                    f"{t.exit_price:.2f}",
                    f"{t.stop_loss:.2f}",
                    f"{t.take_profit:.2f}",
                    t.result.value,
                    f"{t.pnl:.2f}",
                    f"{t.r_multiple:.2f}",
                    t.bars_held,
                    t.trigger_reason,
                ]
            )
    logger.info("Saved trade log to %s", trades_path)

    print("\n=== SUMMARY ===")
    print(f"Symbol/TF: {args.symbol} {args.timeframe}")
    print(f"Window: {bars[0].timestamp} .. {bars[-1].timestamp}")
    print(f"Total trades: {metrics.total_trades}")
    print(f"Win rate: {metrics.win_rate * 100:.2f}%")
    print(f"Net profit: {metrics.net_profit:.2f}")
    print(f"Profit factor: {metrics.profit_factor:.3f}")
    print(f"Max drawdown: {metrics.max_drawdown * 100:.2f}%")
    print(f"Average R: {metrics.average_r:.3f}")
    print(f"Final balance: {result.final_balance:.2f}")
    print(f"Account blown: {result.account_blown}")


def pd_to_aware(bars: list[Bar], date_str: str, end_of_day: bool = False):
    """Parses a YYYY-MM-DD string into a datetime matching bars[0]'s tzinfo."""
    from datetime import datetime

    tz = bars[0].timestamp.tzinfo if bars else None
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt.replace(tzinfo=tz)


if __name__ == "__main__":
    main()
