"""Unit tests for Walk-Forward, Optimization, Monte Carlo, and Robustness modules."""

from datetime import UTC, datetime

import numpy as np
import pytest

from backtest.models import BacktestConfig, BacktestResult, BacktestTrade, TradeResult
from core.models import Bar, SignalDirection, Timeframe
from research.monte_carlo import MonteCarloSimulator
from research.research_optimizer import ParameterOptimizer
from research.robustness import RobustnessTester
from research.stability import ParameterStabilityAnalyzer
from research.walk_forward import WalkForwardRunner
from strategy.continuation import StrategyConfig


@pytest.fixture
def dummy_candles() -> list[Bar]:
    """Generates dummy candle list for testing."""
    return [
        Bar(
            timestamp=datetime(2026, 1, 1, h % 24, 0, tzinfo=UTC),
            open=1.1000,
            high=1.1020,
            low=1.0980,
            close=1.1010,
            volume=1000,
        )
        for h in range(30)
    ]


@pytest.fixture
def dummy_backtest_config() -> BacktestConfig:
    """Generates a standard BacktestConfig with all required positional arguments."""
    return BacktestConfig(
        initial_balance=10000.0,
        risk_per_trade=0.01,
        spread=0.0001,
        commission=5.0,
        slippage=0.0,
    )


def test_walk_forward_runner(dummy_candles: list[Bar], dummy_backtest_config: BacktestConfig) -> None:
    """Tests the walk-forward splitting and validation runner."""
    runner = WalkForwardRunner(
        dummy_candles,
        "EURUSD",
        Timeframe.H1,
        train_size_pct=0.5,
        val_size_pct=0.2,
        step_size_pct=0.1,
    )
    results = runner.run(StrategyConfig(), dummy_backtest_config)
    assert len(results) > 0
    assert "fold" in results[0]
    assert "train_net_profit" in results[0]


def test_parameter_optimizer(dummy_candles: list[Bar]) -> None:
    """Tests the grid and random parameter optimizer."""
    optimizer = ParameterOptimizer(dummy_candles, "EURUSD", Timeframe.H1)
    search_space = {"min_risk_reward_ratio": [1.0, 1.5]}
    best = optimizer.optimize(search_space, max_iter=2)
    assert "min_risk_reward_ratio" in best["best_params"]


def test_parameter_optimizer_returns_best_pnl(dummy_candles: list[Bar]) -> None:
    """Regression test for Bug #20: optimize() must surface a numeric best_pnl.

    dashboard.py reads results["optimization"]["data"]["best_pnl"] to compute the
    Optimization Score; previously optimize() returned only the raw best_params
    dict, so any caller that didn't independently re-simulate the winning params
    would silently leave the Optimization Score stuck at "N/A".
    """
    optimizer = ParameterOptimizer(dummy_candles, "EURUSD", Timeframe.H1)
    search_space = {"min_risk_reward_ratio": [1.0, 1.5]}
    best = optimizer.optimize(search_space, max_iter=2)
    assert "best_pnl" in best
    assert isinstance(best["best_pnl"], float)


def test_parameter_optimizer_grid_within_limit_unchanged(dummy_candles: list[Bar]) -> None:
    """Regression test for Bug #19 fix: small grid searches must behave exactly as before.

    Mirrors the production search space used by run_research.py / run_research_campaign.py
    (3 x 3 = 9 combinations), which is well under the default max_grid_combinations=100 and
    must keep running to completion via method="grid" (the default) with no exception.
    """
    optimizer = ParameterOptimizer(dummy_candles, "EURUSD", Timeframe.H1)
    search_space = {
        "min_risk_reward_ratio": [1.0, 1.5, 2.0],
        "stop_buffer_pips": [3.0, 5.0, 7.0],
    }
    best = optimizer.optimize(search_space)
    assert "min_risk_reward_ratio" in best["best_params"]
    assert "stop_buffer_pips" in best["best_params"]
    assert isinstance(best["best_pnl"], float)


def test_parameter_optimizer_grid_exceeds_limit_raises(dummy_candles: list[Bar]) -> None:
    """Regression test for Bug #19: an unbounded grid must fail fast with a clear error.

    Previously, method="grid" (the default) ran itertools.product over the full search
    space with no cap, so a grid like this (3**5 = 243 combinations) would silently run
    243 full BacktestEngine.run() calls. It must now raise ValueError before simulating
    anything, and the message must state the combination count, the limit, and both
    remedies (raise max_grid_combinations, or switch to method="random").
    """
    optimizer = ParameterOptimizer(dummy_candles, "EURUSD", Timeframe.H1)
    search_space = {f"param_{i}": [1, 2, 3] for i in range(5)}  # 3**5 = 243 combinations

    with pytest.raises(ValueError) as exc_info:
        optimizer.optimize(search_space, method="grid")

    message = str(exc_info.value)
    assert "243" in message
    assert "100" in message
    assert "max_grid_combinations" in message
    assert "random" in message


def test_parameter_optimizer_grid_limit_override_allows_large_grid(dummy_candles: list[Bar]) -> None:
    """A user who raises max_grid_combinations gets the conscious override they asked for.

    101 combinations exceed the default cap of 100, but explicitly raising
    max_grid_combinations must let the same grid run to completion instead of raising.
    """
    optimizer = ParameterOptimizer(dummy_candles, "EURUSD", Timeframe.H1)
    search_space = {"min_risk_reward_ratio": [1.0 + i * 0.01 for i in range(101)]}  # 101 combinations

    best = optimizer.optimize(search_space, method="grid", max_grid_combinations=150)
    assert "min_risk_reward_ratio" in best["best_params"]
    assert isinstance(best["best_pnl"], float)


def test_walk_forward_diagnostics_present_and_reported_for_zero_trade_folds(
    dummy_candles: list[Bar], dummy_backtest_config: BacktestConfig, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for Bug #21: fold results must carry rejection diagnostics, and
    walk_forward_report.md must surface the top rejection reasons when a fold has 0 trades.

    dummy_candles is a flat, unmoving price series, so it never forms the swing/BOS/OB/FVG
    structure BullishContinuationStrategy/BearishContinuationStrategy require -- every fold
    here has 0 trades, which previously left the rejection reason invisible.
    """
    monkeypatch.setenv("TRADEBOT_ARTIFACTS_DIR", str(tmp_path))
    runner = WalkForwardRunner(
        dummy_candles, "EURUSD", Timeframe.H1, train_size_pct=0.5, val_size_pct=0.2, step_size_pct=0.1
    )
    folds = runner.run(StrategyConfig(), dummy_backtest_config)

    assert folds
    assert all(f["train_trades"] == 0 and f["val_trades"] == 0 for f in folds)
    for fold in folds:
        assert "train_diagnostics" in fold
        assert "val_diagnostics" in fold
        assert "0_BullishContinuationStrategy" in fold["val_diagnostics"]

    report = (tmp_path / "walk_forward_report.md").read_text()
    assert "## Zero-Trade Fold Diagnostics" in report
    assert "no_trend" in report


def test_walk_forward_report_unchanged_when_folds_have_trades(
    dummy_candles: list[Bar], tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: folds with trades > 0 must not trigger the Zero-Trade Diagnostics block
    or otherwise change the existing walk_forward_report.md format.
    """
    monkeypatch.setenv("TRADEBOT_ARTIFACTS_DIR", str(tmp_path))
    runner = WalkForwardRunner(dummy_candles, "EURUSD", Timeframe.H1)
    fake_folds = [
        {
            "fold": 1,
            "train_start_date": "2026-01-01 00:00",
            "train_end_date": "2026-01-01 10:00",
            "val_start_date": "2026-01-01 11:00",
            "val_end_date": "2026-01-01 15:00",
            "train_trades": 5,
            "train_net_profit": 120.0,
            "train_win_rate": 0.6,
            "val_trades": 3,
            "val_net_profit": 40.0,
            "val_win_rate": 0.5,
            "train_diagnostics": {"0_BullishContinuationStrategy": {"evaluations": 10, "setups_generated": 5, "rejections": {}}},
            "val_diagnostics": {"0_BullishContinuationStrategy": {"evaluations": 6, "setups_generated": 3, "rejections": {}}},
        }
    ]

    runner._export_artifacts(fake_folds)

    report = (tmp_path / "walk_forward_report.md").read_text()
    assert "Zero-Trade" not in report
    assert "| 1 | 2026-01-01 00:00 to 2026-01-01 10:00" in report


def test_optimizer_diagnostics_present_and_reported_for_zero_trade_trials(
    dummy_candles: list[Bar], tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for Bug #21: trial/best_diagnostics keys must be present, and
    optimization_report.md must be created with top rejection reasons for 0-trade trials.
    """
    monkeypatch.setenv("TRADEBOT_ARTIFACTS_DIR", str(tmp_path))
    optimizer = ParameterOptimizer(dummy_candles, "EURUSD", Timeframe.H1)
    result = optimizer.optimize({"min_risk_reward_ratio": [1.0, 1.5]}, max_iter=2)

    assert "best_diagnostics" in result
    assert "0_BullishContinuationStrategy" in result["best_diagnostics"]

    report_path = tmp_path / "optimization_report.md"
    assert report_path.exists()
    report = report_path.read_text()
    assert "## Zero-Trade Trial Diagnostics" in report
    assert "no_trend" in report


def test_optimization_report_not_created_when_trials_have_trades(
    dummy_candles: list[Bar], tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: no optimization_report.md is written when every trial has trades > 0,
    matching the pre-fix behavior of never producing this file.
    """
    monkeypatch.setenv("TRADEBOT_ARTIFACTS_DIR", str(tmp_path))
    optimizer = ParameterOptimizer(dummy_candles, "EURUSD", Timeframe.H1)
    fake_trials = [
        {
            "trial": 1,
            "parameters": {"min_risk_reward_ratio": 1.0},
            "metrics": {"net_profit": 50.0, "win_rate": 0.6, "total_trades": 4, "profit_factor": 1.5, "max_drawdown": 0.1},
            "diagnostics": {"0_BullishContinuationStrategy": {"evaluations": 10, "setups_generated": 4, "rejections": {}}},
        }
    ]

    optimizer._export_artifacts(fake_trials, {"min_risk_reward_ratio": 1.0}, ["min_risk_reward_ratio"])

    assert not (tmp_path / "optimization_report.md").exists()


def test_robustness_diagnostics_present_and_reported_for_zero_trade_scenarios(
    dummy_candles: list[Bar], dummy_backtest_config: BacktestConfig, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for Bug #21: each stress scenario must carry total_trades/diagnostics,
    and robustness_report.md must surface top rejection reasons for 0-trade scenarios.
    """
    monkeypatch.setenv("TRADEBOT_ARTIFACTS_DIR", str(tmp_path))
    tester = RobustnessTester(dummy_candles, "EURUSD", Timeframe.H1)
    metrics = tester.run(StrategyConfig(), dummy_backtest_config)

    for key in ("baseline", "high_spread", "high_commission", "high_slippage"):
        assert "diagnostics" in metrics[key]
        assert metrics[key]["total_trades"] == 0

    report = (tmp_path / "robustness_report.md").read_text()
    assert "## Zero-Trade Scenario Diagnostics" in report
    assert "**Baseline**: no_trend" in report


def test_robustness_report_unchanged_when_scenarios_have_trades(
    dummy_candles: list[Bar], tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: scenarios with trades > 0 must not trigger the Zero-Trade Diagnostics block."""
    monkeypatch.setenv("TRADEBOT_ARTIFACTS_DIR", str(tmp_path))
    tester = RobustnessTester(dummy_candles, "EURUSD", Timeframe.H1)
    fake_metrics = {
        "baseline": {"net_profit": 100.0, "max_drawdown": 0.1, "total_trades": 5, "diagnostics": {}},
        "high_spread": {"net_profit": 80.0, "max_drawdown": 0.12, "total_trades": 5, "diagnostics": {}},
        "high_commission": {"net_profit": 70.0, "max_drawdown": 0.13, "total_trades": 5, "diagnostics": {}},
        "high_slippage": {"net_profit": 60.0, "max_drawdown": 0.14, "total_trades": 5, "diagnostics": {}},
        "skipped_10pct": {"net_profit": 90.0, "max_drawdown": 0.11},
        "skipped_25pct": {"net_profit": 85.0, "max_drawdown": 0.115},
    }

    tester._export_artifacts(fake_metrics)

    report = (tmp_path / "robustness_report.md").read_text()
    assert "Zero-Trade" not in report


def test_monte_carlo_simulator() -> None:
    """Tests Monte Carlo sequence resampling and risk calculations."""
    simulator = MonteCarloSimulator(n=10)
    trade = BacktestTrade(
        entry_time=datetime.now(),
        exit_time=datetime.now(),
        direction=SignalDirection.BUY,
        entry_price=1.1000,
        exit_price=1.1010,
        stop_loss=1.0990,
        take_profit=1.1020,
        result=TradeResult.WIN,
        pnl=100.0,
        r_multiple=2.0,
    )
    result = BacktestResult(
        trades=(trade,),
        total_profit=100.0,
        win_rate=1.0,
        max_drawdown=0.0,
        profit_factor=999.0,
        initial_balance=10000.0,
        final_balance=10100.0,
        account_blown=False,
    )
    summary = simulator.run(result)
    assert summary["expected_return"] is not None
    assert summary["risk_of_ruin"] == 0.0


def test_robustness_tester(dummy_candles: list[Bar], dummy_backtest_config: BacktestConfig) -> None:
    """Tests the robustness cost stress-testing module."""
    tester = RobustnessTester(dummy_candles, "EURUSD", Timeframe.H1)
    metrics = tester.run(StrategyConfig(), dummy_backtest_config)
    assert "baseline" in metrics
    assert "high_spread" in metrics
    assert "high_commission" in metrics


def test_stability_analyzer(dummy_candles: list[Bar], dummy_backtest_config: BacktestConfig) -> None:
    """Tests parameter stability matrix runs."""
    analyzer = ParameterStabilityAnalyzer(dummy_candles, "EURUSD", Timeframe.H1)
    results = analyzer.run([10, 20], [2.0, 5.0], StrategyConfig(), dummy_backtest_config)
    assert len(results) == 4
    assert results[0]["lookback_bars"] in [10, 20]
def test_stability_grid_within_limit_unchanged(
    dummy_candles: list[Bar], dummy_backtest_config: BacktestConfig
) -> None:
    """Regression test for Bug #51 fix: small grids (well under the default cap) must keep
    running to completion exactly as before -- mirrors the production grid used by
    run_research.py / run_research_campaign.py (5 lookbacks x 5 buffers = 25 combinations).
    """
    analyzer = ParameterStabilityAnalyzer(dummy_candles, "EURUSD", Timeframe.H1)
    results = analyzer.run(
        [10, 15, 20, 25, 30], [2.0, 3.5, 5.0, 6.5, 8.0], StrategyConfig(), dummy_backtest_config
    )
    assert len(results) == 25
    assert all("lookback_bars" in r and "stop_buffer_pips" in r and "net_profit" in r for r in results)


def test_stability_grid_exceeds_limit_raises(
    dummy_candles: list[Bar], dummy_backtest_config: BacktestConfig
) -> None:
    """Regression test for Bug #51: an unbounded stability grid must fail fast with a clear
    error, exactly like ParameterOptimizer.optimize() already does for Bug #19. Previously,
    run() iterated the full lookback_grid x buffer_grid Cartesian product with no size guard --
    a 20x20 grid would silently trigger 400 full BacktestEngine.run() calls with no warning.
    """
    analyzer = ParameterStabilityAnalyzer(dummy_candles, "EURUSD", Timeframe.H1)
    lookback_grid = list(range(1, 21))  # 20 values
    buffer_grid = [float(i) for i in range(1, 21)]  # 20 values -> 400 combinations

    with pytest.raises(ValueError) as exc_info:
        analyzer.run(lookback_grid, buffer_grid, StrategyConfig(), dummy_backtest_config)

    message = str(exc_info.value)
    assert "400" in message
    assert "100" in message
    assert "max_grid_combinations" in message


def test_stability_grid_limit_override_allows_large_grid(
    dummy_candles: list[Bar], dummy_backtest_config: BacktestConfig
) -> None:
    """A user who raises max_grid_combinations gets the conscious override they asked for.

    11x10 = 110 combinations exceed the default cap of 100, but explicitly raising
    max_grid_combinations must let the same grid run to completion instead of raising.
    """
    analyzer = ParameterStabilityAnalyzer(dummy_candles, "EURUSD", Timeframe.H1)
    lookback_grid = list(range(10, 21))  # 11 values
    buffer_grid = [float(i) for i in range(1, 11)]  # 10 values -> 110 combinations

    results = analyzer.run(
        lookback_grid, buffer_grid, StrategyConfig(), dummy_backtest_config, max_grid_combinations=150
    )
    assert len(results) == 110


def test_stability_diagnostics_present_and_reported_for_zero_trade_cells(
    dummy_candles: list[Bar], dummy_backtest_config: BacktestConfig, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for Bug #51: cell results must carry rejection diagnostics, and
    stability_report.md must surface the top rejection reasons when a cell has 0 trades.

    dummy_candles is a flat, unmoving price series, so every cell here has 0 trades, which
    previously left the rejection reason invisible (unlike its 3 sibling research modules).
    """
    monkeypatch.setenv("TRADEBOT_ARTIFACTS_DIR", str(tmp_path))
    analyzer = ParameterStabilityAnalyzer(dummy_candles, "EURUSD", Timeframe.H1)
    results = analyzer.run([10, 20], [2.0, 5.0], StrategyConfig(), dummy_backtest_config)

    assert results
    assert all(r["total_trades"] == 0 for r in results)
    for r in results:
        assert "diagnostics" in r
        assert "0_BullishContinuationStrategy" in r["diagnostics"]

    report = (tmp_path / "stability_report.md").read_text()
    assert "## Zero-Trade Cell Diagnostics" in report
    assert "no_trend" in report


def test_stability_report_not_created_when_cells_have_trades(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: no stability_report.md is written when every cell has trades > 0,
    matching the pre-fix behavior of never producing this file.
    """
    monkeypatch.setenv("TRADEBOT_ARTIFACTS_DIR", str(tmp_path))
    analyzer = ParameterStabilityAnalyzer([], "EURUSD", Timeframe.H1)
    fake_results = [
        {"lookback_bars": 10, "stop_buffer_pips": 2.0, "net_profit": 50.0, "total_trades": 4, "diagnostics": {}},
    ]
    matrix = np.array([[50.0]])

    analyzer._export_artifacts(fake_results, matrix, [10], [2.0])

    assert not (tmp_path / "stability_report.md").exists()
