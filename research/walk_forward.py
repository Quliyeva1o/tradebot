"""Walk-Forward Validation module."""

import csv
from pathlib import Path
from typing import Any

from application.services.market_state_builder import MarketStateBuilder
from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from backtest.report import BacktestReportGenerator
from core.models import Bar, Timeframe
from strategy.continuation import (
    BearishContinuationStrategy,
    BullishContinuationStrategy,
    StrategyConfig,
)
from strategy.strategy_engine import StrategyEngine
from utils.logging import setup_logger

logger = setup_logger("walk_forward")


class WalkForwardRunner:
    """Handles walk-forward data splitting, multi-fold simulations, and segment validation."""

    def __init__(
        self,
        candles: list[Bar],
        symbol: str,
        timeframe: Timeframe,
        train_size_pct: float = 0.6,
        val_size_pct: float = 0.2,
        step_size_pct: float = 0.1,
        expanding: bool = False,
    ) -> None:
        """Initializes the WalkForwardRunner.

        Args:
            candles: Chronological list of candles.
            symbol: Symbol being simulated.
            timeframe: Timeframe of the bars.
            train_size_pct: Fraction of total bars used for training.
            val_size_pct: Fraction of total bars used for validation.
            step_size_pct: Step increment size between folds.
            expanding: If True, train start index remains fixed at 0.
        """
        self.candles = candles
        self.symbol = symbol
        self.timeframe = timeframe
        self.train_size = max(10, int(len(candles) * train_size_pct))
        self.val_size = max(5, int(len(candles) * val_size_pct))
        self.step_size = max(5, int(len(candles) * step_size_pct))
        self.expanding = expanding

    def run(self, strat_config: StrategyConfig, backtest_config: BacktestConfig) -> list[dict[str, Any]]:
        """Executes walk-forward folds chronologically.

        Args:
            strat_config: StrategyConfig parameter inject.
            backtest_config: BacktestConfig parameter inject.

        Returns:
            A list of dictionary summaries for each fold segment.
        """
        n_bars = len(self.candles)
        folds = []
        k = 0

        logger.info("Starting Walk-Forward Validation: train_size=%d, val_size=%d, step_size=%d, mode=%s",
                    self.train_size, self.val_size, self.step_size, "expanding" if self.expanding else "rolling")

        while True:
            # Calculate indices
            train_start = 0 if self.expanding else (k * self.step_size)
            train_end = train_start + self.train_size + ((k * self.step_size) if self.expanding else 0)
            val_start = train_end
            val_end = val_start + self.val_size

            # Boundary check
            if val_end > n_bars:
                break
            if val_start >= n_bars:
                break

            train_candles = self.candles[train_start:train_end]
            val_candles = self.candles[val_start:val_end]

            # Run simulations
            train_metrics = self._simulate(train_candles, strat_config, backtest_config)
            val_metrics = self._simulate(val_candles, strat_config, backtest_config)

            fold_result = {
                "fold": k + 1,
                "train_start_date": train_candles[0].timestamp.strftime("%Y-%m-%d %H:%M"),
                "train_end_date": train_candles[-1].timestamp.strftime("%Y-%m-%d %H:%M"),
                "val_start_date": val_candles[0].timestamp.strftime("%Y-%m-%d %H:%M"),
                "val_end_date": val_candles[-1].timestamp.strftime("%Y-%m-%d %H:%M"),
                "train_trades": train_metrics["total_trades"],
                "train_net_profit": train_metrics["net_profit"],
                "train_win_rate": train_metrics["win_rate"],
                "val_trades": val_metrics["total_trades"],
                "val_net_profit": val_metrics["net_profit"],
                "val_win_rate": val_metrics["win_rate"],
            }
            folds.append(fold_result)
            k += 1

        self._export_artifacts(folds)
        return folds

    def _simulate(self, segment_candles: list[Bar], strat_config: StrategyConfig, backtest_config: BacktestConfig) -> dict[str, Any]:
        """Runs the simulation for a single candle segment."""
        if not segment_candles:
            return {"total_trades": 0, "net_profit": 0.0, "win_rate": 0.0}

        state_builder = MarketStateBuilder(symbol=self.symbol, timeframe=self.timeframe)
        strategy_engine = StrategyEngine()
        strategy_engine.register_strategy(BullishContinuationStrategy(config=strat_config))
        strategy_engine.register_strategy(BearishContinuationStrategy(config=strat_config))

        engine = BacktestEngine(config=backtest_config)
        result = engine.run(segment_candles, strategy_engine, state_builder)

        report_gen = BacktestReportGenerator()
        metrics = report_gen.generate(result)

        return {
            "total_trades": metrics.total_trades,
            "net_profit": metrics.net_profit,
            "win_rate": metrics.win_rate,
        }

    def _export_artifacts(self, folds: list[dict[str, Any]]) -> None:
        """Exports segment outputs to artifacts/ folder."""
        artifacts_dir = Path("c:/Users/Microsol/Desktop/trade/artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # 1. Export walk_forward_summary.csv
        csv_path = artifacts_dir / "walk_forward_summary.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Fold",
                "Train Start",
                "Train End",
                "Val Start",
                "Val End",
                "Train Trades",
                "Train PnL",
                "Train Win Rate",
                "Val Trades",
                "Val PnL",
                "Val Win Rate"
            ])
            for fold in folds:
                writer.writerow([
                    fold["fold"],
                    fold["train_start_date"],
                    fold["train_end_date"],
                    fold["val_start_date"],
                    fold["val_end_date"],
                    fold["train_trades"],
                    f"{fold['train_net_profit']:.2f}",
                    f"{fold['train_win_rate']*100:.2f}%",
                    fold["val_trades"],
                    f"{fold['val_net_profit']:.2f}",
                    f"{fold['val_win_rate']*100:.2f}%"
                ])
        logger.info("Saved walk forward CSV to %s", csv_path)

        # 2. Export walk_forward_report.md
        report_path = artifacts_dir / "walk_forward_report.md"
        md_content = """# Walk-Forward Validation Report

## Segment Performance Metrics

| Fold | Train Window | Val Window | Train Trades | Train PnL | Train Win Rate | Val Trades | Val PnL | Val Win Rate |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
"""
        for fold in folds:
            md_content += (
                f"| {fold['fold']} | {fold['train_start_date']} to {fold['train_end_date']} | "
                f"{fold['val_start_date']} to {fold['val_end_date']} | {fold['train_trades']} | "
                f"${fold['train_net_profit']:.2f} | {fold['train_win_rate']*100:.1f}% | "
                f"{fold['val_trades']} | ${fold['val_net_profit']:.2f} | {fold['val_win_rate']*100:.1f}% |\n"
            )

        with open(report_path, "w") as f:
            f.write(md_content)
        logger.info("Saved walk forward report MD to %s", report_path)
