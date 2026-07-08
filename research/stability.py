"""Parameter Stability Analysis module."""

import csv
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np

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

logger = setup_logger("stability")


class ParameterStabilityAnalyzer:
    """Analyzes strategy performance across parameter grids to locate plateaus and overfitting."""

    def __init__(self, candles: list[Bar], symbol: str, timeframe: Timeframe) -> None:
        """Initializes the ParameterStabilityAnalyzer.

        Args:
            candles: Chronological list of candles.
            symbol: Trading instrument.
            timeframe: Timeframe of the bars.
        """
        self.candles = candles
        self.symbol = symbol
        self.timeframe = timeframe

    def run(
        self,
        lookback_grid: list[int],
        buffer_grid: list[float],
        strat_config: StrategyConfig,
        backtest_config: BacktestConfig,
    ) -> list[dict[str, Any]]:
        """Runs the stability matrix simulation grid.

        Args:
            lookback_grid: List of lookback_bars to test.
            buffer_grid: List of stop_buffer_pips to test.
            strat_config: Baseline StrategyConfig parameters.
            backtest_config: Baseline BacktestConfig parameters.

        Returns:
            A list of dictionary results for each combination.
        """
        logger.info("Starting parameter stability analysis grid: lookbacks=%s, buffers=%s",
                    str(lookback_grid), str(buffer_grid))

        results = []
        matrix = np.zeros((len(lookback_grid), len(buffer_grid)))

        for i, lookback in enumerate(lookback_grid):
            for j, buffer in enumerate(buffer_grid):
                # Build custom configs
                custom_strat = StrategyConfig(
                    pip_size=strat_config.pip_size,
                    lookback_bars=lookback,
                    fvg_proximity_pips=strat_config.fvg_proximity_pips,
                    stop_buffer_pips=buffer,
                    min_risk_reward_ratio=strat_config.min_risk_reward_ratio,
                )

                profit, trades = self._simulate(custom_strat, backtest_config)
                matrix[i, j] = profit
                results.append({
                    "lookback_bars": lookback,
                    "stop_buffer_pips": buffer,
                    "net_profit": profit,
                    "total_trades": trades,
                })

        self._export_artifacts(results, matrix, lookback_grid, buffer_grid)
        return results

    def _simulate(self, strat_config: StrategyConfig, backtest_config: BacktestConfig) -> tuple[float, int]:
        """Runs simulation and returns (net_profit, total_trades)."""
        if not self.candles:
            return 0.0, 0

        state_builder = MarketStateBuilder(symbol=self.symbol, timeframe=self.timeframe)
        strategy_engine = StrategyEngine()
        strategy_engine.register_strategy(BullishContinuationStrategy(config=strat_config))
        strategy_engine.register_strategy(BearishContinuationStrategy(config=strat_config))

        engine = BacktestEngine(config=backtest_config)
        result = engine.run(self.candles, strategy_engine, state_builder)

        report_gen = BacktestReportGenerator()
        metrics = report_gen.generate(result)

        return float(metrics.net_profit), int(metrics.total_trades)

    def _export_artifacts(
        self,
        results: list[dict[str, Any]],
        matrix: np.ndarray,
        lookback_grid: list[int],
        buffer_grid: list[float],
    ) -> None:
        """Exports parameter_stability.csv and stability_heatmap.png."""
        artifacts_dir = Path("c:/Users/Microsol/Desktop/trade/artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # 1. Export parameter_stability.csv (as tabular data)
        csv_path = artifacts_dir / "parameter_stability.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["lookback_bars", "stop_buffer_pips", "net_profit", "total_trades"])
            for res in results:
                writer.writerow([
                    res["lookback_bars"],
                    res["stop_buffer_pips"],
                    f"{res['net_profit']:.2f}",
                    res["total_trades"],
                ])
        logger.info("Saved parameter stability CSV to %s", csv_path)

        # 2. Export stability_heatmap.png
        png_path = artifacts_dir / "stability_heatmap.png"
        fig, ax = plt.subplots(figsize=(6, 5), dpi=300)
        im = ax.imshow(matrix, cmap="RdYlGn", origin="lower", aspect="auto")

        # Set ticks
        ax.set_xticks(np.arange(len(buffer_grid)))
        ax.set_yticks(np.arange(len(lookback_grid)))
        ax.set_xticklabels([f"{x:.1f}" for x in buffer_grid])
        ax.set_yticklabels([str(y) for y in lookback_grid])

        # Add labels
        ax.set_xlabel("Stop Buffer Pips", fontsize=9, color="#2D3748")
        ax.set_ylabel("Lookback Bars", fontsize=9, color="#2D3748")
        ax.set_title("Parameter Stability Plateau Heatmap (PnL)", fontsize=11, fontweight="bold", color="#1A365D")

        # Colorbar
        cbar = fig.colorbar(im, ax=ax)
        cbar.set_label("Net Profit ($)", fontsize=8, color="#2D3748")

        # Annotate cell values
        for i in range(len(lookback_grid)):
            for j in range(len(buffer_grid)):
                val = matrix[i, j]
                ax.text(j, i, f"${val:.0f}", ha="center", va="center",
                        color="black",
                        fontsize=8)

        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")
        plt.tight_layout()
        plt.savefig(png_path)
        plt.close()
        logger.info("Saved parameter stability PNG heatmap to %s", png_path)
