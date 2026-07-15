"""Robustness Testing module."""

import json
import random
from typing import Any

import numpy as np

from application.services.market_state_builder import MarketStateBuilder
from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig, BacktestResult
from backtest.report import BacktestReportGenerator
from core.models import Bar, Timeframe
from strategy.continuation import (
    BearishContinuationStrategy,
    BullishContinuationStrategy,
    StrategyConfig,
)
from strategy.diagnostics import top_rejection_reasons
from strategy.strategy_engine import StrategyEngine
from utils.logging import setup_logger
from utils.paths import get_artifacts_dir

logger = setup_logger("robustness")


class RobustnessTester:
    """Stress tests the strategy against slippage, spread, commission, and random skipped trades."""

    def __init__(self, candles: list[Bar], symbol: str, timeframe: Timeframe) -> None:
        """Initializes the RobustnessTester.

        Args:
            candles: Chronological list of candles.
            symbol: Trading instrument.
            timeframe: Timeframe of the bars.
        """
        self.candles = candles
        self.symbol = symbol
        self.timeframe = timeframe

    def run(self, strat_config: StrategyConfig, base_backtest_config: BacktestConfig) -> dict[str, Any]:
        """Runs the robustness test suite.

        Args:
            strat_config: StrategyConfig parameters.
            base_backtest_config: BacktestConfig parameters.

        Returns:
            A dictionary containing robustness metrics.
        """
        logger.info("Starting strategy robustness stress tests...")

        # 1. Baseline
        base_res = self._simulate(strat_config, base_backtest_config)

        # 2. Spread Stress (3x spread)
        spread_stress_config = BacktestConfig(
            initial_balance=base_backtest_config.initial_balance,
            risk_per_trade=base_backtest_config.risk_per_trade,
            spread=base_backtest_config.spread * 3.0,
            commission=base_backtest_config.commission,
            slippage=base_backtest_config.slippage,
            commission_per_lot=base_backtest_config.commission_per_lot,
            max_holding_bars=base_backtest_config.max_holding_bars,
        )
        spread_res = self._simulate(strat_config, spread_stress_config)

        # 3. Commission Stress (2x commission)
        comm_stress_config = BacktestConfig(
            initial_balance=base_backtest_config.initial_balance,
            risk_per_trade=base_backtest_config.risk_per_trade,
            spread=base_backtest_config.spread,
            commission=base_backtest_config.commission * 2.0,
            slippage=base_backtest_config.slippage,
            commission_per_lot=base_backtest_config.commission_per_lot * 2.0 if base_backtest_config.commission_per_lot is not None else None,
            max_holding_bars=base_backtest_config.max_holding_bars,
        )
        comm_res = self._simulate(strat_config, comm_stress_config)

        # 4. Slippage Stress (3x slippage + 1 pip fixed)
        pip_size = strat_config.pip_size
        slip_stress_config = BacktestConfig(
            initial_balance=base_backtest_config.initial_balance,
            risk_per_trade=base_backtest_config.risk_per_trade,
            spread=base_backtest_config.spread,
            commission=base_backtest_config.commission,
            slippage=base_backtest_config.slippage * 3.0 + 1.0 * pip_size,
            commission_per_lot=base_backtest_config.commission_per_lot,
            max_holding_bars=base_backtest_config.max_holding_bars,
        )
        slip_res = self._simulate(strat_config, slip_stress_config)

        # 5. Skipped Trades Stress (10% and 25% post-processed skips over 100 runs)
        base_engine_res = self._raw_simulate_result(strat_config, base_backtest_config)
        skip_10_profit, skip_10_dd = self._simulate_skips(base_engine_res, skip_prob=0.10)
        skip_25_profit, skip_25_dd = self._simulate_skips(base_engine_res, skip_prob=0.25)

        metrics = {
            "baseline": base_res,
            "high_spread": spread_res,
            "high_commission": comm_res,
            "high_slippage": slip_res,
            "skipped_10pct": {"net_profit": skip_10_profit, "max_drawdown": skip_10_dd},
            "skipped_25pct": {"net_profit": skip_25_profit, "max_drawdown": skip_25_dd},
        }

        self._export_artifacts(metrics)
        return metrics

    def _simulate(self, strat_config: StrategyConfig, backtest_config: BacktestConfig) -> dict[str, Any]:
        """Runs the simulation and returns basic performance values."""
        if not self.candles:
            return {"net_profit": 0.0, "max_drawdown": 0.0, "total_trades": 0, "diagnostics": {}}

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
            "max_drawdown": metrics.max_drawdown,
            "total_trades": metrics.total_trades,
            "diagnostics": strategy_engine.get_diagnostics(),
        }

    def _raw_simulate_result(self, strat_config: StrategyConfig, backtest_config: BacktestConfig) -> BacktestResult:
        """Runs the simulation and returns raw BacktestResult."""
        state_builder = MarketStateBuilder(symbol=self.symbol, timeframe=self.timeframe)
        strategy_engine = StrategyEngine()
        strategy_engine.register_strategy(BullishContinuationStrategy(config=strat_config))
        strategy_engine.register_strategy(BearishContinuationStrategy(config=strat_config))

        engine = BacktestEngine(config=backtest_config)
        return engine.run(self.candles, strategy_engine, state_builder)

    def _simulate_skips(self, result: BacktestResult, skip_prob: float, trials: int = 100) -> tuple[float, float]:
        """Simulates skipping trades randomly with a given probability."""
        trades = result.trades
        initial_balance = result.initial_balance
        if not trades:
            return 0.0, 0.0

        profits = []
        drawdowns = []

        for _ in range(trials):
            balance = initial_balance
            peak = initial_balance
            max_dd = 0.0
            for t in trades:
                if random.random() < skip_prob:
                    continue
                balance += t.pnl
                balance = max(0.0, balance)
                peak = max(peak, balance)
                dd = (peak - balance) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)

            profits.append(balance - initial_balance)
            drawdowns.append(max_dd)

        return float(np.mean(profits)), float(np.mean(drawdowns))

    def _export_artifacts(self, metrics: dict[str, Any]) -> None:
        """Exports robustness_report.md and robustness_metrics.json."""
        artifacts_dir = get_artifacts_dir()
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        # 1. Export robustness_metrics.json
        json_path = artifacts_dir / "robustness_metrics.json"
        with open(json_path, "w") as f:
            json.dump(metrics, f, indent=4)
        logger.info("Saved robustness metrics JSON to %s", json_path)

        # 2. Export robustness_report.md
        report_path = artifacts_dir / "robustness_report.md"

        md_content = f"""# Strategy Robustness & Stress Test Report

Stress tests the continuation strategy against execution friction (spreads, commissions, slippage) and skipped trades.

## Stress Test Results

| Test Scenario | Net Profit | Max Drawdown | PnL vs Baseline | DD vs Baseline |
| --- | ---: | ---: | ---: | ---: |
| **Baseline** | ${metrics['baseline']['net_profit']:.2f} | {metrics['baseline']['max_drawdown']*100:.2f}% | - | - |
| **3x Spread Stress** | ${metrics['high_spread']['net_profit']:.2f} | {metrics['high_spread']['max_drawdown']*100:.2f}% | ${metrics['high_spread']['net_profit'] - metrics['baseline']['net_profit']:+.2f} | {metrics['high_spread']['max_drawdown'] - metrics['baseline']['max_drawdown']:+.2f}% |
| **2x Commission Stress** | ${metrics['high_commission']['net_profit']:.2f} | {metrics['high_commission']['max_drawdown']*100:.2f}% | ${metrics['high_commission']['net_profit'] - metrics['baseline']['net_profit']:+.2f} | {metrics['high_commission']['max_drawdown'] - metrics['baseline']['max_drawdown']:+.2f}% |
| **3x Slippage + 1 Pip Stress** | ${metrics['high_slippage']['net_profit']:.2f} | {metrics['high_slippage']['max_drawdown']*100:.2f}% | ${metrics['high_slippage']['net_profit'] - metrics['baseline']['net_profit']:+.2f} | {metrics['high_slippage']['max_drawdown'] - metrics['baseline']['max_drawdown']:+.2f}% |
| **10% Skipped Trades** | ${metrics['skipped_10pct']['net_profit']:.2f} | {metrics['skipped_10pct']['max_drawdown']*100:.2f}% | ${metrics['skipped_10pct']['net_profit'] - metrics['baseline']['net_profit']:+.2f} | {metrics['skipped_10pct']['max_drawdown'] - metrics['baseline']['max_drawdown']:+.2f}% |
| **25% Skipped Trades** | ${metrics['skipped_25pct']['net_profit']:.2f} | {metrics['skipped_25pct']['max_drawdown']*100:.2f}% | ${metrics['skipped_25pct']['net_profit'] - metrics['baseline']['net_profit']:+.2f} | {metrics['skipped_25pct']['max_drawdown'] - metrics['baseline']['max_drawdown']:+.2f}% |

---

## Robustness Analysis
- **Execution Cost Sensitivity**: The strategy is {"robust" if abs(metrics['high_slippage']['net_profit'] - metrics['baseline']['net_profit']) < 500 else "sensitive"} to slippage increases.
- **Slippage Impact**: Slippage of 3x + 1 pip resulted in a PnL change of **${metrics['high_slippage']['net_profit'] - metrics['baseline']['net_profit']:+.2f}**.
- **Skip Resilience**: Randomly missing 25% of entries resulted in a net profit change of **${metrics['skipped_25pct']['net_profit'] - metrics['baseline']['net_profit']:+.2f}**.
"""

        scenario_labels = {
            "baseline": "Baseline",
            "high_spread": "3x Spread Stress",
            "high_commission": "2x Commission Stress",
            "high_slippage": "3x Slippage + 1 Pip Stress",
        }
        zero_trade_scenarios = [
            key for key in scenario_labels if metrics.get(key, {}).get("total_trades", 0) == 0
        ]
        if zero_trade_scenarios:
            md_content += "\n## Zero-Trade Scenario Diagnostics\n\n"
            for key in zero_trade_scenarios:
                top = top_rejection_reasons(metrics[key].get("diagnostics", {}))
                reasons = ", ".join(f"{r} ({c})" for r, c in top) if top else "no diagnostics recorded"
                md_content += f"**{scenario_labels[key]}**: {reasons}\n\n"

        with open(report_path, "w") as f:
            f.write(md_content)
        logger.info("Saved robustness report MD to %s", report_path)

