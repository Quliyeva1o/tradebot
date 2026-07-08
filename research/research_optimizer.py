"""Parameter Optimization search module."""

import csv
import itertools
import json
import random
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

logger = setup_logger("optimizer")


class ParameterOptimizer:
    """Performs grid and random searches over configuration parameters without mutating files."""

    def __init__(self, candles: list[Bar], symbol: str, timeframe: Timeframe) -> None:
        """Initializes the ParameterOptimizer.

        Args:
            candles: Chronological list of candles.
            symbol: Trading instrument.
            timeframe: Timeframe of the bars.
        """
        self.candles = candles
        self.symbol = symbol
        self.timeframe = timeframe

    def optimize(
        self,
        search_space: dict[str, list[Any]],
        method: str = "grid",
        max_iter: int = 50,
        target_metric: str = "net_profit",
    ) -> dict[str, Any]:
        """Runs the parameter optimization search.

        Args:
            search_space: Dict mapping parameter names to lists of search values.
            method: Optimization method ("grid" or "random").
            max_iter: Max iterations for random search.
            target_metric: Metric to maximize ("net_profit" or "win_rate").

        Returns:
            The best parameters configuration.
        """
        # Determine all combinations
        keys = list(search_space.keys())
        values = list(search_space.values())
        combinations = [dict(zip(keys, prod, strict=True)) for prod in itertools.product(*values)]

        if method == "random":
            if len(combinations) > max_iter:
                combinations = random.sample(combinations, max_iter)

        logger.info("Starting optimization search: method=%s, total_combinations=%d", method, len(combinations))

        trials = []
        best_val = float("-inf")
        best_params = {}

        # Default fallback values for configs
        default_strat = StrategyConfig()
        default_backtest = BacktestConfig(
            initial_balance=10000.0,
            risk_per_trade=0.01,
            spread=0.0001,
            commission=5.0,
            slippage=0.0,
        )

        for i, comb in enumerate(combinations):
            # 1. Build StrategyConfig
            strat_args = {}
            for k in ["pip_size", "lookback_bars", "fvg_proximity_pips", "stop_buffer_pips", "min_risk_reward_ratio"]:
                if k in comb:
                    strat_args[k] = comb[k]
                else:
                    strat_args[k] = getattr(default_strat, k)
            strat_config = StrategyConfig(**strat_args)

            # 2. Build BacktestConfig
            backtest_args = {}
            for k in ["initial_balance", "risk_per_trade", "spread", "commission", "slippage", "commission_per_lot", "max_holding_bars"]:
                if k in comb:
                    backtest_args[k] = comb[k]
                else:
                    if hasattr(default_backtest, k):
                        backtest_args[k] = getattr(default_backtest, k)
            backtest_config = BacktestConfig(**backtest_args)

            # 3. Simulate
            try:
                metrics = self._simulate(strat_config, backtest_config)
                val = metrics.get(target_metric, 0.0)

                trial_res = {
                    "trial": i + 1,
                    "parameters": comb,
                    "metrics": metrics,
                }
                trials.append(trial_res)

                if val > best_val:
                    best_val = val
                    best_params = comb
            except Exception:
                logger.exception("Trial %d failed to execute with params: %s", i + 1, comb)

        self._export_artifacts(trials, best_params, keys)
        return best_params

    def _simulate(self, strat_config: StrategyConfig, backtest_config: BacktestConfig) -> dict[str, Any]:
        """Executes simulation and returns metrics dictionary."""
        if not self.candles:
            return {"net_profit": 0.0, "win_rate": 0.0, "total_trades": 0, "profit_factor": 1.0, "max_drawdown": 0.0}

        state_builder = MarketStateBuilder(symbol=self.symbol, timeframe=self.timeframe)
        strategy_engine = StrategyEngine()
        strategy_engine.register_strategy(BullishContinuationStrategy(config=strat_config))
        strategy_engine.register_strategy(BearishContinuationStrategy(config=strat_config))

        engine = BacktestEngine(config=backtest_config)
        result = engine.run(self.candles, strategy_engine, state_builder)

        report_gen = BacktestReportGenerator()
        metrics = report_gen.generate(result)

        return {
            "net_profit": metrics.net_profit,
            "win_rate": metrics.win_rate,
            "total_trades": metrics.total_trades,
            "profit_factor": metrics.profit_factor,
            "max_drawdown": metrics.max_drawdown,
        }

    def _export_artifacts(self, trials: list[dict[str, Any]], best_params: dict[str, Any], keys: list[str]) -> None:
        """Exports trials CSV, best parameters JSON, and parameter heatmap CSV."""
        artifacts_dir = Path("c:/Users/Microsol/Desktop/trade/artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # 1. Export optimization_results.csv
        csv_path = artifacts_dir / "optimization_results.csv"
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            # Headers: Trial, Params..., Metrics...
            metric_keys = ["net_profit", "win_rate", "total_trades", "profit_factor", "max_drawdown"]
            headers = ["Trial", *keys, *metric_keys]
            writer.writerow(headers)

            for trial in trials:
                params_values = [trial["parameters"].get(k, "") for k in keys]
                metric_values = [trial["metrics"].get(mk, 0.0) for mk in metric_keys]
                writer.writerow([trial["trial"], *params_values, *metric_values])
        logger.info("Saved optimization trials CSV to %s", csv_path)

        # 2. Export best_parameters.json
        json_path = artifacts_dir / "best_parameters.json"
        with open(json_path, "w") as f:
            json.dump(best_params, f, indent=4)
        logger.info("Saved best parameters JSON to %s", json_path)

        # 3. Export parameter_heatmap.csv
        # Heatmap will cross-tabulate first two parameters in keys, or single parameter vs profit if only one exists
        heatmap_path = artifacts_dir / "parameter_heatmap.csv"
        with open(heatmap_path, "w", newline="") as f:
            writer = csv.writer(f)
            if len(keys) >= 2:
                param1 = keys[0]
                param2 = keys[1]
                writer.writerow([param1, param2, "Net Profit"])
                for trial in trials:
                    val1 = trial["parameters"].get(param1, "")
                    val2 = trial["parameters"].get(param2, "")
                    profit = trial["metrics"].get("net_profit", 0.0)
                    writer.writerow([val1, val2, f"{profit:.2f}"])
            else:
                param1 = keys[0] if keys else "None"
                writer.writerow([param1, "Net Profit"])
                for trial in trials:
                    val1 = trial["parameters"].get(param1, "")
                    profit = trial["metrics"].get("net_profit", 0.0)
                    writer.writerow([val1, f"{profit:.2f}"])
        logger.info("Saved parameter heatmap CSV to %s", heatmap_path)
