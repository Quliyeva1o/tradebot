"""Regression tests for Bug #49: run_research_campaign.py's Executive Summary
must be computed from the real wf_results/opt_results/mc_results/rob_results
gathered during the campaign, not hardcoded placeholder literals.
"""

from datetime import datetime
from pathlib import Path

import pytest

import run_research_campaign as campaign_module
from backtest.models import BacktestResult, BacktestTrade, TradeResult
from core.models import Bar, SignalDirection, Timeframe

# The exact literals Bug #49 hardcoded into every campaign_summary regardless of input.
OLD_HARDCODED = {
    "overall_score": 65.0,
    "robustness_score": 75.0,
    "walk_forward_score": 60.0,
    "monte_carlo_score": 85.0,
    "optimization_score": 70.0,
    "profit_factor": 1.45,
    "sharpe": 1.25,
    "risk_of_ruin": 0.0,
    "best_params": "RR=1.5, Buffer=5.0",
}


def _fold(val_net_profit: float, train_net_profit: float = 100.0) -> dict:
    return {
        "fold": 1,
        "train_net_profit": train_net_profit,
        "val_net_profit": val_net_profit,
    }


def _rob_scenario(symbol: str, baseline_profit: float, retention: float) -> dict:
    """Builds a synthetic RobustnessTester.run()-shaped result retaining `retention`
    fraction of baseline profit under every stress scenario."""
    stressed = {"net_profit": baseline_profit * retention}
    return {
        "symbol": symbol,
        "baseline": {"net_profit": baseline_profit},
        "high_spread": stressed,
        "high_commission": stressed,
        "high_slippage": stressed,
        "skipped_10pct": stressed,
        "skipped_25pct": stressed,
    }


class TestWalkForwardScore:
    def test_all_folds_profitable_scores_100(self) -> None:
        wf_results = [_fold(val_net_profit=50.0), _fold(val_net_profit=80.0)]
        assert campaign_module._compute_walk_forward_score(wf_results) == 100.0

    def test_all_folds_losing_scores_0(self) -> None:
        wf_results = [_fold(val_net_profit=-50.0), _fold(val_net_profit=-10.0)]
        assert campaign_module._compute_walk_forward_score(wf_results) == 0.0

    def test_mixed_folds_scores_proportionally(self) -> None:
        wf_results = [_fold(val_net_profit=50.0), _fold(val_net_profit=-10.0)]
        assert campaign_module._compute_walk_forward_score(wf_results) == 50.0

    def test_empty_scores_0(self) -> None:
        assert campaign_module._compute_walk_forward_score([]) == 0.0


class TestOptimizationScore:
    def test_all_symbols_profitable_scores_100(self) -> None:
        opt_results = [{"symbol": "EURUSD", "best_pnl": 200.0}, {"symbol": "GBPUSD", "best_pnl": 5.0}]
        assert campaign_module._compute_optimization_score(opt_results) == 100.0

    def test_all_symbols_unprofitable_scores_0(self) -> None:
        opt_results = [{"symbol": "EURUSD", "best_pnl": -20.0}]
        assert campaign_module._compute_optimization_score(opt_results) == 0.0


class TestMonteCarloScore:
    def test_low_ruin_positive_return_scores_high(self) -> None:
        mc_results = [{"symbol": "EURUSD", "risk_of_ruin": 0.5, "expected_return": 100.0}]
        score = campaign_module._compute_monte_carlo_score(mc_results)
        assert score == pytest.approx(99.5)

    def test_negative_return_halves_the_score(self) -> None:
        mc_results = [{"symbol": "EURUSD", "risk_of_ruin": 0.5, "expected_return": -100.0}]
        score = campaign_module._compute_monte_carlo_score(mc_results)
        assert score == pytest.approx(99.5 * 0.5)

    def test_high_ruin_scores_low(self) -> None:
        mc_results = [{"symbol": "EURUSD", "risk_of_ruin": 90.0, "expected_return": 100.0}]
        assert campaign_module._compute_monte_carlo_score(mc_results) == pytest.approx(10.0)


class TestRobustnessScore:
    def test_full_retention_scores_100(self) -> None:
        rob_results = [_rob_scenario("EURUSD", baseline_profit=1000.0, retention=1.0)]
        assert campaign_module._compute_robustness_score(rob_results) == pytest.approx(100.0)

    def test_half_retention_scores_50(self) -> None:
        rob_results = [_rob_scenario("EURUSD", baseline_profit=1000.0, retention=0.5)]
        assert campaign_module._compute_robustness_score(rob_results) == pytest.approx(50.0)

    def test_unprofitable_baseline_scores_0(self) -> None:
        rob_results = [_rob_scenario("EURUSD", baseline_profit=-100.0, retention=1.0)]
        assert campaign_module._compute_robustness_score(rob_results) == 0.0


class TestSelectBestParamsString:
    def test_picks_highest_pnl_symbol(self) -> None:
        opt_results = [
            {"symbol": "EURUSD", "best_params": {"min_risk_reward_ratio": 1.0}, "best_pnl": 10.0},
            {"symbol": "XAUUSD", "best_params": {"min_risk_reward_ratio": 2.0, "stop_buffer_pips": 7.0}, "best_pnl": 500.0},
        ]
        result = campaign_module._select_best_params_string(opt_results)
        assert result.startswith("XAUUSD:")
        assert "min_risk_reward_ratio=2.0" in result
        assert result != OLD_HARDCODED["best_params"]

    def test_empty_results_returns_na(self) -> None:
        assert "N/A" in campaign_module._select_best_params_string([])


class TestExecutiveSummaryDiffersByScenario:
    """Core Bug #49 regression: two different synthetic result sets ('good' and 'bad')
    must produce different Executive Summary numbers, and neither may collide with the
    old hardcoded literals across the board.
    """

    def _build_summary(
        self, wf_results: list[dict], opt_results: list[dict], mc_results: list[dict], rob_results: list[dict]
    ) -> dict:
        walk_forward_score = campaign_module._compute_walk_forward_score(wf_results)
        optimization_score = campaign_module._compute_optimization_score(opt_results)
        monte_carlo_score = campaign_module._compute_monte_carlo_score(mc_results)
        robustness_score = campaign_module._compute_robustness_score(rob_results)
        overall_score = (
            0.30 * walk_forward_score
            + 0.25 * robustness_score
            + 0.25 * monte_carlo_score
            + 0.20 * optimization_score
        )
        risk_of_ruin = sum(mc.get("risk_of_ruin", 0.0) for mc in mc_results) / len(mc_results)
        best_params = campaign_module._select_best_params_string(opt_results)
        strengths, weaknesses = campaign_module._build_strengths_and_weaknesses(
            walk_forward_score=walk_forward_score,
            optimization_score=optimization_score,
            robustness_score=robustness_score,
            profit_factor=2.0,
            sharpe=1.5,
            risk_of_ruin=risk_of_ruin,
            worst_drawdown=0.1,
        )
        return {
            "overall_score": overall_score,
            "walk_forward_score": walk_forward_score,
            "optimization_score": optimization_score,
            "monte_carlo_score": monte_carlo_score,
            "robustness_score": robustness_score,
            "risk_of_ruin": risk_of_ruin,
            "best_params": best_params,
            "strengths": strengths,
            "weaknesses": weaknesses,
        }

    def test_good_and_bad_scenarios_produce_different_summaries(self) -> None:
        good_summary = self._build_summary(
            wf_results=[_fold(val_net_profit=80.0), _fold(val_net_profit=60.0)],
            opt_results=[{"symbol": "EURUSD", "best_params": {"min_risk_reward_ratio": 2.0}, "best_pnl": 500.0}],
            mc_results=[{"symbol": "EURUSD", "risk_of_ruin": 0.2, "expected_return": 300.0}],
            rob_results=[_rob_scenario("EURUSD", baseline_profit=1000.0, retention=0.9)],
        )
        bad_summary = self._build_summary(
            wf_results=[_fold(val_net_profit=-80.0), _fold(val_net_profit=-60.0)],
            opt_results=[{"symbol": "EURUSD", "best_params": {"min_risk_reward_ratio": 1.0}, "best_pnl": -50.0}],
            mc_results=[{"symbol": "EURUSD", "risk_of_ruin": 45.0, "expected_return": -300.0}],
            rob_results=[_rob_scenario("EURUSD", baseline_profit=1000.0, retention=0.1)],
        )

        # The two scenarios must diverge on every computed score.
        assert good_summary["overall_score"] > bad_summary["overall_score"]
        assert good_summary["walk_forward_score"] > bad_summary["walk_forward_score"]
        assert good_summary["optimization_score"] > bad_summary["optimization_score"]
        assert good_summary["monte_carlo_score"] > bad_summary["monte_carlo_score"]
        assert good_summary["robustness_score"] > bad_summary["robustness_score"]
        assert good_summary["risk_of_ruin"] < bad_summary["risk_of_ruin"]

        # Neither scenario may reproduce the old static literals (the whole point of the fix).
        assert good_summary["overall_score"] != OLD_HARDCODED["overall_score"]
        assert bad_summary["overall_score"] != OLD_HARDCODED["overall_score"]
        assert good_summary["walk_forward_score"] != OLD_HARDCODED["walk_forward_score"]
        assert bad_summary["walk_forward_score"] != OLD_HARDCODED["walk_forward_score"]

        # Narrative text must reflect the real numbers, not generic canned prose.
        assert any("walk-forward" in s.lower() for s in good_summary["strengths"])
        assert any("walk-forward" in w.lower() for w in bad_summary["weaknesses"])
        assert good_summary["strengths"] != bad_summary["strengths"]
        assert good_summary["weaknesses"] != bad_summary["weaknesses"]


class TestComputePortfolioMetrics:
    """Exercises _compute_portfolio_metrics, which recomputes profit_factor/sharpe for the
    Executive Summary directly from combined trades (independent of the resume-state phases)."""

    def _trade(self, pnl: float, day_offset: int) -> BacktestTrade:
        entry = datetime(2026, 1, 1 + day_offset, 9, 0)
        exit_ = datetime(2026, 1, 1 + day_offset, 11, 0)
        return BacktestTrade(
            entry_time=entry,
            exit_time=exit_,
            direction=SignalDirection.BUY,
            entry_price=1.1000,
            exit_price=1.1000 + pnl / 100000.0,
            stop_loss=1.0950,
            take_profit=1.1100,
            result=TradeResult.WIN if pnl > 0 else TradeResult.LOSS,
            pnl=pnl,
            r_multiple=pnl / 100.0,
        )

    def test_computes_profit_factor_from_real_combined_trades(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        history_dir = tmp_path / "data" / "history"
        history_dir.mkdir(parents=True)
        (history_dir / "EURUSD_H1_2024.csv").write_text("time,open,high,low,close,volume\n")

        trades = (
            self._trade(200.0, 0),
            self._trade(100.0, 1),
            self._trade(-60.0, 2),
            self._trade(-40.0, 3),
        )

        class FakeProvider:
            def __init__(self, filepath: Path) -> None:
                self.filepath = filepath

            def load(self) -> list[Bar]:
                return [Bar(timestamp=datetime(2024, 1, 1), open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0)]

        class FakeEngine:
            def __init__(self, config) -> None:
                self.config = config

            def run(self, bars, strategy_engine, state_builder) -> BacktestResult:
                return BacktestResult(
                    trades=trades,
                    total_profit=sum(t.pnl for t in trades),
                    win_rate=0.0,
                    max_drawdown=0.0,
                    profit_factor=0.0,
                    initial_balance=10000.0,
                )

        monkeypatch.setattr(campaign_module, "PROJECT_ROOT", tmp_path)
        monkeypatch.setattr(campaign_module, "CSVDataProvider", FakeProvider)
        monkeypatch.setattr(campaign_module, "BacktestEngine", FakeEngine)

        profit_factor, sharpe = campaign_module._compute_portfolio_metrics(
            symbols=["EURUSD"], years=[2024], timeframe="H1", timeframe_enum=Timeframe.H1
        )

        # gross_profit=300, gross_loss=100 -> profit_factor = 3.0, nowhere near the old 1.45 constant.
        assert profit_factor == pytest.approx(3.0)
        assert profit_factor != OLD_HARDCODED["profit_factor"]
        assert sharpe is not None
        assert sharpe != OLD_HARDCODED["sharpe"]

    def test_returns_neutral_fallback_when_no_data_files_exist(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(campaign_module, "PROJECT_ROOT", tmp_path)

        profit_factor, sharpe = campaign_module._compute_portfolio_metrics(
            symbols=["EURUSD"], years=[2024], timeframe="H1", timeframe_enum=Timeframe.H1
        )

        assert profit_factor == 1.0
        assert sharpe is None
