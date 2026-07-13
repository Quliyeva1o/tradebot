"""Monte Carlo Simulation module."""

import random
from typing import Any

import numpy as np

from backtest.models import BacktestResult
from utils.logging import setup_logger
from utils.paths import get_artifacts_dir

logger = setup_logger("monte_carlo")


class MonteCarloSimulator:
    """Runs trade-level Monte Carlo validation with randomized reshuffling and execution noise."""

    def __init__(self, n: int = 1000) -> None:
        """Initializes the MonteCarloSimulator.

        Args:
            n: Number of simulation trials to run.
        """
        self.n = n

    def run(self, result: BacktestResult, pip_size: float = 0.0001) -> dict[str, Any]:
        """Runs the Monte Carlo simulation.

        Args:
            result: Baseline backtest execution result.
            pip_size: Price value of a single pip.

        Returns:
            A dictionary containing simulation summary metrics.
        """
        trades = result.trades
        initial_balance = result.initial_balance

        if not trades:
            logger.warning("No trades available for Monte Carlo simulation.")
            summary = {
                "expected_return": 0.0,
                "worst_drawdown": 0.0,
                "confidence_interval_95": (initial_balance, initial_balance),
                "risk_of_ruin": 0.0,
                "prob_new_high": 0.0,
            }
            self._export_report(summary, initial_balance)
            return summary

        logger.info("Starting Monte Carlo simulation: n=%d trials on %d baseline trades", self.n, len(trades))

        final_balances = []
        max_drawdowns = []
        ruin_count = 0
        new_high_count = 0

        for _ in range(self.n):
            # Resample trades with replacement (bootstrapping)
            resampled = random.choices(trades, k=len(trades))

            balance = initial_balance
            peak = initial_balance
            max_dd = 0.0
            equity_curve = [balance]

            # Apply random spread/slippage noise per trade
            for t in resampled:
                # Noise penalty: random value from 0 to 1 pip, multiplied by position size
                noise_pips = random.uniform(0.0, 1.5)
                # Position size fallback to 0.1 lots
                pos_size = t.position_size if t.position_size > 0 else 0.1
                # Spread/slippage noise cost
                noise_cost = noise_pips * pip_size * pos_size * 100000.0  # 1 lot = 100k standard contract size

                pnl = t.pnl - noise_cost
                balance += pnl
                # Negative balance protection
                balance = max(0.0, balance)

                equity_curve.append(balance)
                peak = max(peak, balance)
                dd = (peak - balance) / peak if peak > 0 else 0.0
                max_dd = max(max_dd, dd)

            final_balances.append(balance)
            max_drawdowns.append(max_dd)

            # Risk of Ruin threshold: balance drops below 30% of initial balance
            if min(equity_curve) < 0.3 * initial_balance:
                ruin_count += 1

            # Probability of New Equity High: final balance is the peak of the run
            if balance >= peak and balance > initial_balance:
                new_high_count += 1

        # Calculate metrics
        expected_return = np.mean(final_balances) - initial_balance
        worst_drawdown = np.max(max_drawdowns)
        ci_lower = np.percentile(final_balances, 2.5)
        ci_upper = np.percentile(final_balances, 97.5)
        risk_of_ruin = (ruin_count / self.n) * 100
        prob_new_high = (new_high_count / self.n) * 100

        summary = {
            "expected_return": expected_return,
            "worst_drawdown": worst_drawdown,
            "confidence_interval_95": (ci_lower, ci_upper),
            "risk_of_ruin": risk_of_ruin,
            "prob_new_high": prob_new_high,
        }

        self._export_report(summary, initial_balance)
        return summary

    def _export_report(self, summary: dict[str, Any], initial_balance: float) -> None:
        """Exports the Monte Carlo summary report as Markdown."""
        artifacts_dir = get_artifacts_dir()
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        report_path = artifacts_dir / "monte_carlo_report.md"
        ci = summary["confidence_interval_95"]

        md_content = f"""# Monte Carlo Simulation Report

Runs a randomized trade resampling simulation (N={self.n}) to test equity curve robustness against sequence risk and execution noise.

## Simulation Metrics

| Parameter | Value |
| --- | ---: |
| Expected Return | ${summary['expected_return']:.2f} ({(summary['expected_return'] / initial_balance)*100:+.2f}%) |
| Worst Case Max Drawdown | {summary['worst_drawdown']*100:.2f}% |
| 95% Confidence Interval Lower Bound | ${ci[0]:.2f} |
| 95% Confidence Interval Upper Bound | ${ci[1]:.2f} |
| Risk of Ruin (<30% Account size) | {summary['risk_of_ruin']:.2f}% |
| Probability of Final Equity High | {summary['prob_new_high']:.2f}% |

---

## Conclusion
- Expectation of return is {"positive" if summary['expected_return'] > 0 else "negative or neutral"}.
- Risk of ruin is {"very low (<1%)" if summary['risk_of_ruin'] < 1.0 else "concerning (>1%)"}.
- Sequence variance demonstrates that the trading strategy is {"stable" if summary['worst_drawdown'] < 0.25 else "exposed to high tail risk"}.
"""
        with open(report_path, "w") as f:
            f.write(md_content)
        logger.info("Saved Monte Carlo report MD to %s", report_path)
