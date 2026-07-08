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
    assert "min_risk_reward_ratio" in best


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
