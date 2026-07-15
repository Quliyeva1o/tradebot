"""Unit tests for Walk-Forward, Optimization, Monte Carlo, and Robustness modules."""

from datetime import UTC, datetime

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
