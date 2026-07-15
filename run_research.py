#!/usr/bin/env python3
"""Standard Runner for Walks Forward, Optimizations, Monte Carlo, and Robustness testing."""

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

# Add project root to python path to prevent import issues
sys.path.append(str(Path(__file__).parent.resolve()))

from application.services.market_state_builder import MarketStateBuilder
from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig
from core.models import Bar, Timeframe
from data.csv_provider import CSVDataProvider
from mt5.history_downloader import MT5HistoryDownloader
from research.dashboard import ResearchDashboard
from research.monte_carlo import MonteCarloSimulator
from research.research_optimizer import ParameterOptimizer
from research.robustness import RobustnessTester
from research.stability import ParameterStabilityAnalyzer
from research.walk_forward import WalkForwardRunner
from strategy.continuation import (
    BearishContinuationStrategy,
    BullishContinuationStrategy,
    StrategyConfig,
)
from strategy.strategy_engine import StrategyEngine
from utils.logging import setup_logger
from utils.paths import PROJECT_ROOT

logger = setup_logger("run_research")


def load_config(config_path: str) -> dict[str, Any]:
    """Loads configuration from a YAML file."""
    logger.info("Loading configuration from %s", config_path)
    with open(config_path) as f:
        config: dict[str, Any] = yaml.safe_load(f)
    return config


def check_and_get_data(config: dict[str, Any]) -> list[Bar]:
    """Checks if the data file exists, downloading it via MT5 if necessary."""
    symbol = config["symbol"]
    tf_str = config["timeframe"]

    # Handle datetime parsing from config
    start_dt = config["start_date"]
    end_dt = config["end_date"]
    if isinstance(start_dt, str):
        start_dt = datetime.strptime(start_dt, "%Y-%m-%d %H:%M:%S")
    if isinstance(end_dt, str):
        end_dt = datetime.strptime(end_dt, "%Y-%m-%d %H:%M:%S")

    # Target path format: data/history/{symbol}_{timeframe}_{year}.csv
    export_dir = PROJECT_ROOT / "data" / "history"
    file_name = f"{symbol}_{tf_str}_{start_dt.year}.csv"
    csv_path = export_dir / file_name

    fallback_path = PROJECT_ROOT / "data" / f"{symbol}_{tf_str}.csv"

    if not csv_path.exists():
        logger.info("Target data file %s not found. Invoking MT5 downloader...", file_name)
        downloader = MT5HistoryDownloader()
        downloaded_path = downloader.download(
            symbol=symbol,
            timeframe=tf_str,
            start_date=start_dt,
            end_date=end_dt,
        )
        if downloaded_path:
            csv_path = Path(downloaded_path)
        else:
            logger.warning("MT5 download failed. Attempting fallback to %s", fallback_path)
            if fallback_path.exists():
                csv_path = fallback_path
            else:
                raise FileNotFoundError(
                    f"No historical data file found at {csv_path} and fallback {fallback_path} does not exist."
                )
    else:
        logger.info("Using existing historical data file: %s", csv_path)

    provider = CSVDataProvider(filepath=csv_path)
    bars = provider.load()

    if bars:
        tz = bars[0].timestamp.tzinfo
        start_dt_aware = start_dt.replace(tzinfo=tz)
        end_dt_aware = end_dt.replace(tzinfo=tz)
        filtered_bars = [
            bar for bar in bars if start_dt_aware <= bar.timestamp <= end_dt_aware
        ]
    else:
        filtered_bars = []
    logger.info(
        "Loaded %d bars total, filtered to %d bars within %s and %s",
        len(bars),
        len(filtered_bars),
        start_dt,
        end_dt,
    )
    return filtered_bars


def main() -> None:
    """Research Validation loop runner."""
    logger.info("==================================================")
    logger.info("Starting Research Validation Suite (Sprint 12)")
    logger.info("==================================================")

    config_path = str(PROJECT_ROOT / "config" / "backtest.yaml")
    try:
        config = load_config(config_path)
        candles = check_and_get_data(config)
    except Exception:
        logger.exception("Failed to load configuration or data:")
        sys.exit(1)

    symbol = config["symbol"]
    tf_str = config["timeframe"]
    try:
        timeframe_enum = Timeframe[tf_str]
    except KeyError:
        timeframe_enum = Timeframe.H1

    # Base Immutable Configuration layers
    strat_config = StrategyConfig(
        lookback_bars=20,
        fvg_proximity_pips=50.0,
        stop_buffer_pips=5.0,
        min_risk_reward_ratio=1.0,
    )

    backtest_config = BacktestConfig(
        initial_balance=float(config["initial_balance"]),
        risk_per_trade=float(config["risk_per_trade"]),
        spread=float(config["spread"]),
        commission=float(config["commission"]),
        slippage=float(config["slippage"]),
        max_holding_bars=config.get("max_holding_bars"),
        commission_per_lot=config.get("commission_per_lot"),
    )

    results: dict[str, Any] = {}

    # 1. Walk Forward Validation
    logger.info("\n--- [STAGE 1] Walk Forward Validation ---")
    try:
        wf_runner = WalkForwardRunner(
            candles=candles,
            symbol=symbol,
            timeframe=timeframe_enum,
            train_size_pct=0.6,
            val_size_pct=0.2,
            step_size_pct=0.1,
            expanding=False,
        )
        folds = wf_runner.run(strat_config, backtest_config)
        results["walk_forward"] = {
            "status": "SUCCESS",
            "data": folds,
            "message": f"Successfully completed {len(folds)} validation folds.",
        }
    except Exception as e:
        logger.exception("Walk Forward Stage Failed:")
        results["walk_forward"] = {"status": "FAILED", "message": str(e)}

    # 2. Parameter Optimization
    logger.info("\n--- [STAGE 2] Parameter Optimization ---")
    try:
        optimizer = ParameterOptimizer(candles, symbol, timeframe_enum)
        search_space = {
            "min_risk_reward_ratio": [1.0, 1.5, 2.0],
            "stop_buffer_pips": [3.0, 5.0, 7.0],
        }
        optim_result = optimizer.optimize(search_space, method="grid")
        best_params = optim_result["best_params"]

        results["optimization"] = {
            "status": "SUCCESS",
            "data": {
                "best_params": best_params,
                "best_pnl": optim_result["best_pnl"],
            },
            "message": f"Optimal Parameters: RR={best_params.get('min_risk_reward_ratio')}, StopBuffer={best_params.get('stop_buffer_pips')}",
        }
    except Exception as e:
        logger.exception("Parameter Optimization Stage Failed:")
        results["optimization"] = {"status": "FAILED", "message": str(e)}

    # 3. Monte Carlo Simulation
    logger.info("\n--- [STAGE 3] Monte Carlo Simulation ---")
    try:
        # Run baseline simulation to obtain trades list
        state_builder = MarketStateBuilder(symbol=symbol, timeframe=timeframe_enum)
        strategy_engine = StrategyEngine()
        strategy_engine.register_strategy(BullishContinuationStrategy(config=strat_config))
        strategy_engine.register_strategy(BearishContinuationStrategy(config=strat_config))
        engine = BacktestEngine(config=backtest_config)
        base_res = engine.run(candles, strategy_engine, state_builder)

        mc_simulator = MonteCarloSimulator(n=1000)
        mc_summary = mc_simulator.run(base_res, pip_size=strat_config.pip_size)
        results["monte_carlo"] = {
            "status": "SUCCESS",
            "data": mc_summary,
            "message": f"Expected return: ${mc_summary['expected_return']:.2f}, Ruin risk: {mc_summary['risk_of_ruin']:.1f}%",
        }
    except Exception as e:
        logger.exception("Monte Carlo Stage Failed:")
        results["monte_carlo"] = {"status": "FAILED", "message": str(e)}

    # 4. Robustness Analysis
    logger.info("\n--- [STAGE 4] Robustness Testing ---")
    try:
        robustness_tester = RobustnessTester(candles, symbol, timeframe_enum)
        rob_metrics = robustness_tester.run(strat_config, backtest_config)
        results["robustness"] = {
            "status": "SUCCESS",
            "data": rob_metrics,
            "message": f"Baseline PnL: ${rob_metrics['baseline']['net_profit']:.2f}, Stress Slippage PnL: ${rob_metrics['high_slippage']['net_profit']:.2f}",
        }
    except Exception as e:
        logger.exception("Robustness Testing Stage Failed:")
        results["robustness"] = {"status": "FAILED", "message": str(e)}

    # 5. Parameter Stability
    logger.info("\n--- [STAGE 5] Parameter Stability Analysis ---")
    try:
        stability_analyzer = ParameterStabilityAnalyzer(candles, symbol, timeframe_enum)
        stability_grid = stability_analyzer.run(
            lookback_grid=[10, 15, 20, 25, 30],
            buffer_grid=[2.0, 3.5, 5.0, 6.5, 8.0],
            strat_config=strat_config,
            backtest_config=backtest_config,
        )
        results["stability"] = {
            "status": "SUCCESS",
            "data": stability_grid,
            "message": "Stability matrix heatmaps generated successfully.",
        }
    except Exception as e:
        logger.exception("Parameter Stability Stage Failed:")
        results["stability"] = {"status": "FAILED", "message": str(e)}

    # 6. Research Dashboard
    logger.info("\n--- [STAGE 6] Research Dashboard Compilation ---")
    try:
        dashboard = ResearchDashboard()
        dashboard.run(symbol, tf_str, results)
        logger.info("Research summary report compiled successfully.")
    except Exception:
        logger.exception("Research Dashboard Generation Failed:")

    logger.info("==================================================")
    logger.info("Research Validation Suite Finished Execution.")
    logger.info("==================================================")


if __name__ == "__main__":
    main()
