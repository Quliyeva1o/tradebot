#!/usr/bin/env python3
"""Modular Institutional Research Campaign Runner (Sprint 12/13)."""

import argparse
import csv
import hashlib
import json
import os
import platform
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import numpy as np
import yaml

# Add project root to python path to prevent import issues
sys.path.append(str(Path(__file__).parent.resolve()))

import MetaTrader5 as mt5  # noqa: N813

# ReportLab PDF imports
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from application.services.market_state_builder import MarketStateBuilder
from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig, BacktestResult, BacktestTrade
from backtest.report import BacktestReportGenerator
from core.models import Bar, Timeframe
from data.csv_provider import CSVDataProvider
from mt5.history_downloader import MT5HistoryDownloader
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
from utils.paths import PROJECT_ROOT, get_artifacts_dir

logger = setup_logger("run_campaign")


def get_git_info() -> tuple[str, str]:
    """Retrieves current git commit hash and active branch."""
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode("utf-8").strip()
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"]).decode("utf-8").strip()
        return commit, branch
    except Exception:
        return "N/A", "N/A"


def get_dataset_hash(file_paths: list[Path]) -> str:
    """Computes a SHA-256 hash of all concatenated data files."""
    sha256 = hashlib.sha256()
    for path in sorted(file_paths):
        if path.exists():
            with open(path, "rb") as hash_file:
                while chunk := hash_file.read(8192):
                    sha256.update(chunk)
    return sha256.hexdigest()[:16]


def validate_data_quality(symbol: str, csv_path: Path, timeframe: str) -> dict[str, Any]:
    """Performs candle-level data quality validation checks."""
    errors: list[str] = []
    report = {
        "symbol": symbol,
        "valid": True,
        "total_candles": 0,
        "duplicate_candles": 0,
        "invalid_ohlc": 0,
        "weekend_gaps": 0,
        "data_coverage_pct": 100.0,
        "errors": errors
    }

    if not csv_path.exists():
        report["valid"] = False
        errors.append("Data file does not exist.")
        return report

    try:
        provider = CSVDataProvider(filepath=csv_path)
        bars = provider.load()
        report["total_candles"] = len(bars)

        if not bars:
            report["valid"] = False
            errors.append("Data file is empty.")
            return report

        timestamps = [b.timestamp for b in bars]
        # 1. Duplicates check
        if len(timestamps) != len(set(timestamps)):
            seen = set()
            duplicates = []
            for t in timestamps:
                if t in seen:
                    duplicates.append(t)
                else:
                    seen.add(t)
            report["duplicate_candles"] = len(duplicates)
            errors.append(f"Detected {len(duplicates)} duplicate candle timestamps.")
            report["valid"] = False

        # 2. Invalid OHLC bounds
        invalid_ohlc_count = 0
        for b in bars:
            if b.high < b.low or b.open < 0 or b.close < 0 or b.open > b.high or b.close > b.high or b.open < b.low or b.close < b.low:
                invalid_ohlc_count += 1
        if invalid_ohlc_count > 0:
            report["invalid_ohlc"] = invalid_ohlc_count
            errors.append(f"Detected {invalid_ohlc_count} candles with invalid OHLC boundaries.")
            report["valid"] = False

        # 3. Timeframe Coverage & Weekend Gaps
        start_ts = min(timestamps)
        end_ts = max(timestamps)
        total_hours = (end_ts - start_ts).total_seconds() / 3600.0
        # Exclude standard weekends (~48 hours per week)
        weeks = total_hours / 168.0
        expected_candles = total_hours - (weeks * 48.0)
        expected_candles = max(1.0, expected_candles)

        coverage = (len(bars) / expected_candles) * 100.0
        report["data_coverage_pct"] = min(100.0, max(0.0, coverage))

        # Check gap constraints
        weekend_gaps_count = 0
        for i in range(1, len(bars)):
            diff = bars[i].timestamp - bars[i - 1].timestamp
            if diff > timedelta(hours=1):
                # Is it a standard weekend (Friday 22:00 to Sunday 22:00)?
                prev_dt = bars[i - 1].timestamp
                # Day 4 is Friday, day 6 is Sunday
                if prev_dt.weekday() == 4 and prev_dt.hour >= 20:
                    continue
                weekend_gaps_count += 1

        report["weekend_gaps"] = weekend_gaps_count
        if weekend_gaps_count > 0:
            errors.append(f"Detected {weekend_gaps_count} unexpected gaps outside standard weekend closures.")

    except Exception as e:
        report["valid"] = False
        errors.append(f"Integrity check crashed: {e}")

    return report


def _compute_portfolio_metrics(
    symbols: list[str], years: list[int], timeframe: str, timeframe_enum: Timeframe
) -> tuple[float, float | None]:
    """Recomputes combined profit factor and Sharpe ratio across all symbols' full-history baselines.

    Runs an independent default-config backtest per symbol from the CSV files on disk and pools every
    resulting trade into one chronologically-sorted portfolio, so the Executive Summary reflects the
    current dataset/strategy even when phases 1-6 were restored from a resume checkpoint (whose state
    file does not retain individual trades).

    Returns:
        A tuple of (profit_factor, sharpe_ratio). sharpe_ratio is None if there is not enough
        distinct-day trade data to compute a standard deviation of daily returns.
    """
    all_trades: list[BacktestTrade] = []
    for symbol in symbols:
        symbol_bars: list[Bar] = []
        for year in years:
            csv_path = (PROJECT_ROOT / "data" / "history") / f"{symbol}_{timeframe}_{year}.csv"
            if csv_path.exists():
                provider = CSVDataProvider(filepath=csv_path)
                year_bars = provider.load()
                provider.validate(year_bars)
                symbol_bars.extend(year_bars)

        if not symbol_bars:
            continue

        state_builder = MarketStateBuilder(symbol=symbol, timeframe=timeframe_enum)
        strategy_engine = StrategyEngine()
        strategy_engine.register_strategy(BullishContinuationStrategy())
        strategy_engine.register_strategy(BearishContinuationStrategy())
        engine = BacktestEngine(config=BacktestConfig(10000.0, 0.01, 0.0001, 5.0, 0.0))
        result = engine.run(symbol_bars, strategy_engine, state_builder)
        all_trades.extend(result.trades)

    if not all_trades:
        return 1.0, None

    sorted_trades = tuple(sorted(all_trades, key=lambda t: t.entry_time))
    combined_result = BacktestResult(
        trades=sorted_trades,
        total_profit=sum(t.pnl for t in sorted_trades),
        # win_rate/max_drawdown/profit_factor below are placeholders: BacktestReportGenerator.generate()
        # recomputes all performance metrics directly from `trades`/`initial_balance`, never reads these.
        win_rate=0.0,
        max_drawdown=0.0,
        profit_factor=0.0,
        initial_balance=10000.0,
    )
    metrics = BacktestReportGenerator().generate(combined_result)
    return metrics.profit_factor, metrics.sharpe_ratio


def _compute_walk_forward_score(wf_results: list[dict[str, Any]]) -> float:
    """Scores walk-forward robustness as the percentage of validation folds that were profitable out-of-sample."""
    if not wf_results:
        return 0.0
    positive_val_folds = sum(1 for fold in wf_results if fold.get("val_net_profit", 0.0) > 0)
    return (positive_val_folds / len(wf_results)) * 100.0


def _compute_optimization_score(opt_results: list[dict[str, Any]]) -> float:
    """Scores optimization success as the percentage of symbols whose best trial was profitable."""
    if not opt_results:
        return 0.0
    positive = sum(1 for o in opt_results if o.get("best_pnl", 0.0) > 0)
    return (positive / len(opt_results)) * 100.0


def _compute_monte_carlo_score(mc_results: list[dict[str, Any]]) -> float:
    """Scores Monte Carlo robustness from risk of ruin, penalized further if expected return is non-positive."""
    if not mc_results:
        return 0.0
    scores = []
    for mc in mc_results:
        risk_of_ruin = mc.get("risk_of_ruin", 100.0)
        expected_return = mc.get("expected_return", 0.0)
        safety = max(0.0, 100.0 - risk_of_ruin)
        scores.append(safety if expected_return > 0 else safety * 0.5)
    return sum(scores) / len(scores)


def _compute_robustness_score(rob_results: list[dict[str, Any]]) -> float:
    """Scores robustness as the average fraction of baseline profit retained under stress scenarios."""
    if not rob_results:
        return 0.0
    stress_keys = ["high_spread", "high_commission", "high_slippage", "skipped_10pct", "skipped_25pct"]
    scores = []
    for r in rob_results:
        baseline_profit = r.get("baseline", {}).get("net_profit", 0.0)
        if baseline_profit <= 0:
            scores.append(0.0)
            continue
        ratios = [
            max(0.0, min(1.0, r.get(key, {}).get("net_profit", 0.0) / baseline_profit))
            for key in stress_keys
        ]
        scores.append((sum(ratios) / len(ratios)) * 100.0)
    return sum(scores) / len(scores)


def _select_best_params_string(opt_results: list[dict[str, Any]]) -> str:
    """Formats the highest-best_pnl symbol's optimized parameters from real opt_results."""
    if not opt_results:
        return "N/A (no optimization results available)"
    best_entry = max(opt_results, key=lambda o: o.get("best_pnl", float("-inf")))
    params = best_entry.get("best_params", {})
    if not params:
        return f"{best_entry['symbol']}: (no profitable parameters found)"
    params_str = ", ".join(f"{k}={v}" for k, v in params.items())
    return f"{best_entry['symbol']}: {params_str}"


def _build_strengths_and_weaknesses(
    walk_forward_score: float,
    optimization_score: float,
    robustness_score: float,
    profit_factor: float,
    sharpe: float | None,
    risk_of_ruin: float,
    worst_drawdown: float,
) -> tuple[list[str], list[str]]:
    """Builds strengths/weaknesses lists via simple threshold rules over real computed metrics.

    Deliberately not free-form NLG: each line is a fixed template gated by an if/else on an actual
    computed value, so the wording always matches the numbers in the same report.
    """
    strengths: list[str] = []
    weaknesses: list[str] = []

    if profit_factor > 1.2:
        strengths.append(f"Combined profit factor of {profit_factor:.2f} across all segment backtests indicates a real edge.")
    elif profit_factor < 1.0:
        weaknesses.append(f"Combined profit factor of {profit_factor:.2f} is below breakeven across all segment backtests.")

    if sharpe is not None and sharpe > 1.0:
        strengths.append(f"Sharpe ratio of {sharpe:.2f} indicates favorable risk-adjusted returns.")
    elif sharpe is not None and sharpe < 0.5:
        weaknesses.append(f"Sharpe ratio of {sharpe:.2f} indicates weak risk-adjusted returns.")

    if risk_of_ruin < 1.0:
        strengths.append(f"Monte Carlo risk of ruin averaged {risk_of_ruin:.2f}% across symbols, indicating low tail risk.")
    else:
        weaknesses.append(f"Monte Carlo risk of ruin averaged {risk_of_ruin:.2f}% across symbols, indicating material tail risk.")

    if walk_forward_score >= 60.0:
        strengths.append(f"{walk_forward_score:.0f}% of walk-forward validation folds were profitable out-of-sample.")
    else:
        weaknesses.append(f"Only {walk_forward_score:.0f}% of walk-forward validation folds were profitable out-of-sample.")

    if robustness_score >= 60.0:
        strengths.append(f"Retained {robustness_score:.0f}% of baseline profit on average under spread/commission/slippage/skip stress tests.")
    else:
        weaknesses.append(f"Retained only {robustness_score:.0f}% of baseline profit on average under stress tests, indicating sensitivity to execution friction.")

    if optimization_score >= 60.0:
        strengths.append(f"Parameter optimization found a profitable configuration on {optimization_score:.0f}% of symbols tested.")
    else:
        weaknesses.append(f"Parameter optimization found a profitable configuration on only {optimization_score:.0f}% of symbols tested.")

    if worst_drawdown < 0.20:
        strengths.append(f"Worst segment drawdown of {worst_drawdown*100:.1f}% stayed within a conservative risk budget.")
    else:
        weaknesses.append(f"Worst segment drawdown of {worst_drawdown*100:.1f}% exceeded a conservative risk budget.")

    if not strengths:
        strengths.append("No metric cleared its strength threshold this run.")
    if not weaknesses:
        weaknesses.append("No metric fell below its weakness threshold this run.")

    return strengths, weaknesses


def main() -> None:
    """Main research campaign execution entrypoint."""
    parser = argparse.ArgumentParser(description="Sprint 12/13 Validation Research Campaign")
    parser.add_argument("--seed", type=int, default=None, help="Override fixed random seed")
    parser.add_argument("--force", action="store_true", help="Restart research campaign from scratch")
    args = parser.parse_args()

    # Load campaign configuration
    config_path = PROJECT_ROOT / "config" / "research_campaign.yaml"
    if not config_path.exists():
        logger.error("Configuration file not found at %s", config_path)
        sys.exit(1)

    with open(config_path, encoding="utf-8") as campaign_file:
        campaign_config = yaml.safe_load(campaign_file)

    # 1. Seed initialization
    campaign_seed = args.seed if args.seed is not None else campaign_config.get("seed", 42)
    random.seed(campaign_seed)
    np.random.seed(campaign_seed)
    logger.info("Initialized Campaign Seed: %d", campaign_seed)

    # Determine paths and timestamps
    time_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    campaign_dir = get_artifacts_dir() / "research_campaign" / time_str
    latest_dir = get_artifacts_dir() / "research_campaign" / "latest"
    campaign_dir.mkdir(parents=True, exist_ok=True)
    latest_dir.mkdir(parents=True, exist_ok=True)

    state_path = get_artifacts_dir() / "campaign_state.json"
    state = {}
    if state_path.exists() and not args.force:
        logger.info("Resuming research campaign from last checkpoint...")
        with open(state_path, encoding="utf-8") as state_file:
            state = json.load(state_file)

    # System information metadata collection
    git_commit, git_branch = get_git_info()
    mt5_build = "N/A"
    try:
        if mt5.initialize():
            mt5_build = str(mt5.version()[1])
            mt5.shutdown()
    except Exception:
        pass

    metadata = {
        "git_commit": git_commit,
        "git_branch": git_branch,
        "python_version": platform.python_version(),
        "mt5_version": mt5_build,
        "campaign_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "seed": campaign_seed,
    }

    start_time_seconds = time.perf_counter()

    symbols = campaign_config.get("symbols", ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"])
    years = campaign_config.get("years", [2020, 2021, 2022, 2023, 2024, 2025])
    timeframe = campaign_config.get("timeframe", "H1")

    # 2. Phase 0: Setup & Data Quality checks
    data_quality_reports = []
    file_paths: list[Path] = []

    try:
        timeframe_enum = Timeframe[timeframe]
    except KeyError:
        timeframe_enum = Timeframe.H1

    # Connect to MT5 to download any missing rate files
    downloader = MT5HistoryDownloader()

    # Pre-download and validate dataset files
    for symbol in symbols:
        for year in years:
            start_date = datetime(year, 1, 1)
            end_date = datetime(year, 12, 31)

            csv_name = f"{symbol}_{timeframe}_{year}.csv"
            csv_path = (PROJECT_ROOT / "data" / "history") / csv_name

            if not csv_path.exists():
                logger.info("Downloading historical rates for %s [%s] for %d...", symbol, timeframe, year)
                try:
                    downloaded = downloader.download(symbol, timeframe, start_date, end_date)
                    if downloaded:
                        csv_path = Path(downloaded)
                except Exception as e:
                    logger.error("Failed downloading %s for %d: %s", symbol, year, e)

            if csv_path.exists():
                file_paths.append(csv_path)
                dq_report = validate_data_quality(symbol, csv_path, timeframe)
                data_quality_reports.append(dq_report)
            else:
                data_quality_reports.append({
                    "symbol": f"{symbol}_{year}",
                    "valid": False,
                    "errors": ["Dataset file failed to download/locate from MT5 terminal."]
                })

    # Save Data Quality report
    metadata["dataset_hash"] = get_dataset_hash(file_paths)
    dq_report_path = campaign_dir / "data_quality_report.md"
    dq_md = "# Campaign Data Quality Report\n\n"
    dq_md += f"- **Dataset SHA-256 Hash**: `{metadata['dataset_hash']}`\n\n"
    dq_md += "| Symbol/Segment | Valid | Total Candles | Duplicates | Invalid OHLC | Coverage | Diagnostics |\n"
    dq_md += "| --- | --- | ---: | ---: | ---: | ---: | --- |\n"
    for rep in data_quality_reports:
        valid_icon = "✅ PASS" if rep.get("valid") else "❌ FAIL"
        err_msg = "; ".join(rep.get("errors", [])) if rep.get("errors") else "-"
        dq_md += f"| {rep['symbol']} | {valid_icon} | {rep.get('total_candles', 0)} | {rep.get('duplicate_candles', 0)} | {rep.get('invalid_ohlc', 0)} | {rep.get('data_coverage_pct', 100.0):.1f}% | {err_msg} |\n"

    with open(dq_report_path, "w", encoding="utf-8") as dq_file:
        dq_file.write(dq_md)
    logger.info("Saved data quality report to %s", dq_report_path)

    # 3. Phase 1: Download & Segment Backtests
    segment_results = []
    if "phase_1" not in state:
        logger.info("\n--- Phase 1: Downloading & Segment Backtests ---")
        strat_config = StrategyConfig()

        for symbol in symbols:
            for year in years:
                csv_name = f"{symbol}_{timeframe}_{year}.csv"
                csv_path = (PROJECT_ROOT / "data" / "history") / csv_name

                if not csv_path.exists():
                    logger.warning("Skipping year segment %s for symbol %s (download failed).", year, symbol)
                    continue

                provider = CSVDataProvider(filepath=csv_path)
                bars = provider.load()
                provider.validate(bars)

                if not bars:
                    continue

                state_builder = MarketStateBuilder(symbol=symbol, timeframe=timeframe_enum)
                strategy_engine = StrategyEngine()
                strategy_engine.register_strategy(BullishContinuationStrategy(config=strat_config))
                strategy_engine.register_strategy(BearishContinuationStrategy(config=strat_config))

                backtest_config = BacktestConfig(
                    initial_balance=10000.0,
                    risk_per_trade=0.01,
                    spread=0.0001,
                    commission=5.0,
                    slippage=0.0,
                )

                engine = BacktestEngine(config=backtest_config)
                res = engine.run(bars, strategy_engine, state_builder)

                segment_res = {
                    "symbol": symbol,
                    "year": year,
                    "net_profit": res.final_balance - res.initial_balance,
                    "trades": len(res.trades),
                    "win_rate": res.win_rate,
                    "max_drawdown": res.max_drawdown,
                }
                segment_results.append(segment_res)

        state["phase_1"] = "SUCCESS"
        state["segment_results"] = segment_results
        with open(state_path, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file)
    else:
        logger.info("Skipping Phase 1 (Restored completed segment backtests).")
        segment_results = state.get("segment_results", [])

    # 4. Phase 2: Walk-Forward Validation
    wf_results = []
    if "phase_2" not in state:
        logger.info("\n--- Phase 2: Walk Forward Validation ---")
        for symbol in symbols:
            all_bars = []
            for year in years:
                csv_path = (PROJECT_ROOT / "data" / "history") / f"{symbol}_{timeframe}_{year}.csv"
                if csv_path.exists():
                    provider = CSVDataProvider(filepath=csv_path)
                    year_bars = provider.load()
                    provider.validate(year_bars)
                    all_bars.extend(year_bars)

            if len(all_bars) < 30:
                logger.warning("Insufficient data for full Walk-Forward Validation on %s", symbol)
                continue

            wf_runner = WalkForwardRunner(
                candles=all_bars,
                symbol=symbol,
                timeframe=timeframe_enum,
                train_size_pct=0.6,
                val_size_pct=0.2,
                step_size_pct=0.1,
                expanding=False,
            )
            folds = wf_runner.run(StrategyConfig(), BacktestConfig(10000.0, 0.01, 0.0001, 5.0, 0.0))
            for fold_data in folds:
                fold_data["symbol"] = symbol
                wf_results.append(fold_data)

        state["phase_2"] = "SUCCESS"
        state["wf_results"] = wf_results
        with open(state_path, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file)
    else:
        logger.info("Skipping Phase 2 (Restored walk-forward folds).")
        wf_results = state.get("wf_results", [])

    # 5. Phase 3: Grid & Random Parameter Optimization
    opt_results = []
    if "phase_3" not in state:
        logger.info("\n--- Phase 3: Grid & Random Parameter Optimization ---")
        for symbol in symbols:
            all_bars = []
            for year in years:
                csv_path = (PROJECT_ROOT / "data" / "history") / f"{symbol}_{timeframe}_{year}.csv"
                if csv_path.exists():
                    provider = CSVDataProvider(filepath=csv_path)
                    year_bars = provider.load()
                    provider.validate(year_bars)
                    all_bars.extend(year_bars)

            if len(all_bars) < 20:
                continue

            optimizer = ParameterOptimizer(all_bars, symbol, timeframe_enum)
            search_space = {
                "min_risk_reward_ratio": [1.0, 1.5, 2.0],
                "stop_buffer_pips": [3.0, 5.0, 7.0],
            }
            optim_result = optimizer.optimize(search_space, method="grid")
            opt_results.append({
                "symbol": symbol,
                "best_params": optim_result["best_params"],
                "best_pnl": optim_result["best_pnl"],
            })

        state["phase_3"] = "SUCCESS"
        state["opt_results"] = opt_results
        with open(state_path, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file)
    else:
        logger.info("Skipping Phase 3 (Restored optimizer parameters).")
        opt_results = state.get("opt_results", [])

    # 6. Phase 4: Monte Carlo Simulations
    mc_results = []
    if "phase_4" not in state:
        logger.info("\n--- Phase 4: Monte Carlo Simulations ---")
        for symbol in symbols:
            all_bars = []
            for year in years:
                csv_path = (PROJECT_ROOT / "data" / "history") / f"{symbol}_{timeframe}_{year}.csv"
                if csv_path.exists():
                    provider = CSVDataProvider(filepath=csv_path)
                    year_bars = provider.load()
                    provider.validate(year_bars)
                    all_bars.extend(year_bars)

            if len(all_bars) < 20:
                continue

            state_builder = MarketStateBuilder(symbol=symbol, timeframe=timeframe_enum)
            strategy_engine = StrategyEngine()
            strategy_engine.register_strategy(BullishContinuationStrategy())
            strategy_engine.register_strategy(BearishContinuationStrategy())
            engine = BacktestEngine(config=BacktestConfig(10000.0, 0.01, 0.0001, 5.0, 0.0))
            base_res = engine.run(all_bars, strategy_engine, state_builder)

            mc_simulator = MonteCarloSimulator(n=1000)
            mc_summary = mc_simulator.run(base_res)
            mc_summary["symbol"] = symbol
            mc_results.append(mc_summary)

        state["phase_4"] = "SUCCESS"
        state["mc_results"] = mc_results
        with open(state_path, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file)
    else:
        logger.info("Skipping Phase 4 (Restored Monte Carlo metrics).")
        mc_results = state.get("mc_results", [])

    # 7. Phase 5: Robustness stress testing
    rob_results = []
    if "phase_5" not in state:
        logger.info("\n--- Phase 5: Robustness Testing ---")
        for symbol in symbols:
            all_bars = []
            for year in years:
                csv_path = (PROJECT_ROOT / "data" / "history") / f"{symbol}_{timeframe}_{year}.csv"
                if csv_path.exists():
                    provider = CSVDataProvider(filepath=csv_path)
                    year_bars = provider.load()
                    provider.validate(year_bars)
                    all_bars.extend(year_bars)

            if len(all_bars) < 20:
                continue

            tester = RobustnessTester(all_bars, symbol, timeframe_enum)
            metrics_rob = tester.run(StrategyConfig(), BacktestConfig(10000.0, 0.01, 0.0001, 5.0, 0.0))
            metrics_rob["symbol"] = symbol
            rob_results.append(metrics_rob)

        state["phase_5"] = "SUCCESS"
        state["rob_results"] = rob_results
        with open(state_path, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file)
    else:
        logger.info("Skipping Phase 5 (Restored robustness stresses).")
        rob_results = state.get("rob_results", [])

    # 8. Phase 6: Parameter Stability Analysis
    stability_results = []
    if "phase_6" not in state:
        logger.info("\n--- Phase 6: Parameter Stability Analysis ---")
        for symbol in symbols:
            all_bars = []
            for year in years:
                csv_path = (PROJECT_ROOT / "data" / "history") / f"{symbol}_{timeframe}_{year}.csv"
                if csv_path.exists():
                    provider = CSVDataProvider(filepath=csv_path)
                    year_bars = provider.load()
                    provider.validate(year_bars)
                    all_bars.extend(year_bars)

            if len(all_bars) < 20:
                continue

            analyzer = ParameterStabilityAnalyzer(all_bars, symbol, timeframe_enum)
            res_stability = analyzer.run(
                lookback_grid=[10, 15, 20, 25, 30],
                buffer_grid=[2.0, 3.5, 5.0, 6.5, 8.0],
                strat_config=StrategyConfig(),
                backtest_config=BacktestConfig(10000.0, 0.01, 0.0001, 5.0, 0.0)
            )
            stability_results.append({
                "symbol": symbol,
                "data": res_stability,
            })

        state["phase_6"] = "SUCCESS"
        state["stability_results"] = stability_results
        with open(state_path, "w", encoding="utf-8") as state_file:
            json.dump(state, state_file)
    else:
        logger.info("Skipping Phase 6 (Restored stability matrices).")
        stability_results = state.get("stability_results", [])

    # 9. Phase 7 & 8: Executive Summary Report & CSV sheets exports
    logger.info("\n--- Phase 7 & 8: Cross Market validation and final reports compilation ---")

    elapsed_time = time.perf_counter() - start_time_seconds
    metadata["duration_seconds"] = elapsed_time

    # Compute Campaign summary metrics
    total_net_profit = sum(x["net_profit"] for x in segment_results)
    avg_win_rate = np.mean([x["win_rate"] for x in segment_results]) if segment_results else 0.0
    worst_drawdown = max([x["max_drawdown"] for x in segment_results]) if segment_results else 1.0

    verdict = "NEEDS IMPROVEMENT"
    if total_net_profit > 1000.0 and worst_drawdown < 0.20:
        verdict = "READY FOR FORWARD TEST"
    elif worst_drawdown > 0.40 or total_net_profit < 0:
        verdict = "NOT ROBUST"

    # Bug #49 fix: every field below is computed from the real wf_results/opt_results/mc_results/
    # rob_results gathered in phases 2-5 above, instead of being a hardcoded placeholder.
    walk_forward_score = _compute_walk_forward_score(wf_results)
    optimization_score = _compute_optimization_score(opt_results)
    monte_carlo_score = _compute_monte_carlo_score(mc_results)
    robustness_score = _compute_robustness_score(rob_results)
    overall_score = (
        0.30 * walk_forward_score
        + 0.25 * robustness_score
        + 0.25 * monte_carlo_score
        + 0.20 * optimization_score
    )

    profit_factor, sharpe = _compute_portfolio_metrics(symbols, years, timeframe, timeframe_enum)
    sharpe_display = sharpe if sharpe is not None else 0.0
    risk_of_ruin = (
        float(np.mean([mc.get("risk_of_ruin", 0.0) for mc in mc_results])) if mc_results else 0.0
    )
    best_params_str = _select_best_params_string(opt_results)

    strengths, weaknesses = _build_strengths_and_weaknesses(
        walk_forward_score=walk_forward_score,
        optimization_score=optimization_score,
        robustness_score=robustness_score,
        profit_factor=profit_factor,
        sharpe=sharpe,
        risk_of_ruin=risk_of_ruin,
        worst_drawdown=worst_drawdown,
    )

    if verdict == "READY FOR FORWARD TEST":
        next_action = (
            "Proceed to forward testing with tight-spread accounts, and verify execution "
            "slippage margins in a live-adjacent (demo) environment."
        )
    elif verdict == "NOT ROBUST":
        next_action = (
            "Do not proceed to forward testing. Net profit or drawdown breached the minimum "
            "robustness bar -- revisit strategy logic/parameters before re-running this campaign."
        )
    else:
        next_action = (
            "Address the weaknesses listed above before proceeding to forward testing; "
            "re-run this campaign after changes to confirm improvement."
        )

    campaign_summary: dict[str, Any] = {
        "verdict": verdict,
        "overall_score": overall_score,
        "robustness_score": robustness_score,
        "walk_forward_score": walk_forward_score,
        "monte_carlo_score": monte_carlo_score,
        "optimization_score": optimization_score,
        "max_drawdown": worst_drawdown,
        "profit_factor": profit_factor,
        "sharpe": sharpe_display,
        "risk_of_ruin": risk_of_ruin,
        "best_params": best_params_str,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "next_action": next_action,
    }

    # Compare with previous campaign if available
    previous_summary: dict[str, Any] | None = None
    prev_json_path = latest_dir / "research_campaign.json"
    if prev_json_path.exists():
        try:
            with open(prev_json_path, encoding="utf-8") as prev_file:
                previous_summary = json.load(prev_file)
        except Exception:
            pass

    # Save artifacts
    # 1. cross_market_summary.csv
    with open(campaign_dir / "cross_market_summary.csv", "w", newline="", encoding="utf-8") as cross_file:
        writer = csv.writer(cross_file)
        writer.writerow(["Symbol", "Year", "Net Profit", "Trades", "Win Rate", "Max DD"])
        for seg in segment_results:
            writer.writerow([seg["symbol"], seg["year"], f"{seg['net_profit']:.2f}", seg["trades"], f"{seg['win_rate']*100:.2f}%", f"{seg['max_drawdown']*100:.2f}%"])

    # 2. walk_forward_summary.csv
    shutil.copy(get_artifacts_dir() / "walk_forward_summary.csv", campaign_dir / "walk_forward_summary.csv")

    # 3. optimization_summary.csv
    with open(campaign_dir / "optimization_summary.csv", "w", newline="", encoding="utf-8") as opt_file:
        writer = csv.writer(opt_file)
        writer.writerow(["Symbol", "Best parameters"])
        for o in opt_results:
            writer.writerow([o["symbol"], str(o["best_params"])])

    # 4. monte_carlo_summary.csv
    with open(campaign_dir / "monte_carlo_summary.csv", "w", newline="", encoding="utf-8") as mc_file:
        writer = csv.writer(mc_file)
        writer.writerow(["Symbol", "Expected Return", "Risk of Ruin", "Prob New High"])
        for mc in mc_results:
            writer.writerow([mc["symbol"], f"{mc['expected_return']:.2f}", f"{mc['risk_of_ruin']:.2f}%", f"{mc['prob_new_high']:.2f}%"])

    # 5. robustness_summary.csv
    with open(campaign_dir / "robustness_summary.csv", "w", newline="", encoding="utf-8") as rob_file:
        writer = csv.writer(rob_file)
        writer.writerow(["Symbol", "Baseline profit", "High spread profit", "High slippage profit"])
        for r in rob_results:
            writer.writerow([r["symbol"], f"{r['baseline']['net_profit']:.2f}", f"{r['high_spread']['net_profit']:.2f}", f"{r['high_slippage']['net_profit']:.2f}"])

    # 6. stability_summary.csv
    with open(campaign_dir / "stability_summary.csv", "w", newline="", encoding="utf-8") as stab_file:
        writer = csv.writer(stab_file)
        writer.writerow(["Symbol", "Tested combinations", "Avg profit"])
        for s in stability_results:
            avg_profit = np.mean([x["net_profit"] for x in s["data"]]) if s["data"] else 0.0
            writer.writerow([s["symbol"], len(s["data"]), f"{avg_profit:.2f}"])

    # 7. Save research_campaign.json
    campaign_db = {
        "metadata": metadata,
        "summary": campaign_summary,
        "segment_results": segment_results,
        "walk_forward": wf_results,
        "optimization": opt_results,
        "monte_carlo": mc_results,
        "robustness": rob_results,
        "stability": stability_results,
    }
    with open(campaign_dir / "research_campaign.json", "w", encoding="utf-8") as db_file:
        json.dump(campaign_db, db_file, indent=4)

    # 8. Save research_campaign.md
    report_md_path = campaign_dir / "research_campaign.md"
    md_report = f"""# Executive Research & Validation Campaign Report

## Executive Summary
- **Final Verdict**: **{campaign_summary['verdict']}**
- **Overall Score**: {campaign_summary['overall_score']:.1f}/100
- **Robustness Score**: {campaign_summary['robustness_score']:.1f}/100
- **Walk-Forward Score**: {campaign_summary['walk_forward_score']:.1f}/100
- **Monte Carlo Score**: {campaign_summary['monte_carlo_score']:.1f}/100
- **Optimization Score**: {campaign_summary['optimization_score']:.1f}/100
- **Average Win Rate**: {avg_win_rate * 100:.2f}%
- **Maximum Drawdown**: {campaign_summary['max_drawdown']*100:.2f}%
- **Profit Factor**: {campaign_summary['profit_factor']:.2f}
- **Sharpe**: {campaign_summary['sharpe']:.2f}
- **Risk of Ruin**: {campaign_summary['risk_of_ruin']:.1f}%
- **Best Parameter Set**: {campaign_summary['best_params']}

### Top 5 Strengths
"""
    for str_item in campaign_summary["strengths"]:
        md_report += f"- {str_item}\n"

    md_report += "\n### Top 5 Weaknesses\n"
    for weak_item in campaign_summary["weaknesses"]:
        md_report += f"- {weak_item}\n"

    md_report += f"\n- **Recommended Next Action**: {campaign_summary['next_action']}\n"

    # Compare with previous campaign
    if previous_summary:
        prev_pnl = sum(x["net_profit"] for x in previous_summary.get("segment_results", []))
        pnl_diff = total_net_profit - prev_pnl
        md_report += f"""
---

## Comparison Against Previous Campaign
- **Previous Net Profit**: ${prev_pnl:.2f}
- **Current Net Profit**: ${total_net_profit:.2f}
- **Delta**: ${pnl_diff:+.2f}
- **Verdict Comparison**: Previous: **{previous_summary.get('summary', {}).get('verdict')}** | Current: **{campaign_summary['verdict']}**
"""

    md_report += f"""
---

## Campaign Metadata
- **Git Commit**: `{metadata['git_commit']}` (Branch: `{metadata['git_branch']}`)
- **Python Version**: `{metadata['python_version']}`
- **MT5 Build**: `{metadata['mt5_version']}`
- **Execution Seed**: `{metadata['seed']}`
- **Dataset Hash**: `{metadata['dataset_hash']}`
- **Duration**: {metadata['duration_seconds']:.2f} seconds
"""

    with open(report_md_path, "w", encoding="utf-8") as md_file:
        md_file.write(md_report)

    # 9. Save PDF using ReportLab
    pdf_report_path = campaign_dir / "research_campaign.pdf"
    doc = SimpleDocTemplate(
        str(pdf_report_path),
        pagesize=letter,
        leftMargin=36,
        rightMargin=36,
        topMargin=36,
        bottomMargin=36,
    )
    story = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#1A365D"),
        spaceAfter=15,
        alignment=0,
    )
    h1_style = ParagraphStyle(
        "Heading1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#2B6CB0"),
        spaceBefore=10,
        spaceAfter=6,
        keepWithNext=True,
    )
    body_style = ParagraphStyle(
        "Body",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#2D3748"),
    )
    bold_body = ParagraphStyle("BoldBody", parent=body_style, fontName="Helvetica-Bold")

    # Cover Page Executive summary
    story.append(Paragraph("Executive Research & Validation Campaign Report", title_style))
    story.append(Spacer(1, 5))

    story.append(Paragraph("Executive Scorecard Summary", h1_style))
    scorecard_data = [
        [Paragraph("Scorecard Item", bold_body), Paragraph("Value", bold_body)],
        ["Final Verdict", verdict],
        ["Overall Score", f"{campaign_summary['overall_score']:.1f}/100"],
        ["Robustness Score", f"{campaign_summary['robustness_score']:.1f}/100"],
        ["Walk-Forward Score", f"{campaign_summary['walk_forward_score']:.1f}/100"],
        ["Monte Carlo Score", f"{campaign_summary['monte_carlo_score']:.1f}/100"],
        ["Optimization Score", f"{campaign_summary['optimization_score']:.1f}/100"],
        ["Average Win Rate", f"{avg_win_rate * 100:.2f}%"],
        ["Maximum Drawdown", f"{campaign_summary['max_drawdown']*100:.2f}%"],
        ["Profit Factor", f"{campaign_summary['profit_factor']:.2f}"],
        ["Sharpe", f"{campaign_summary['sharpe']:.2f}"],
        ["Risk of Ruin", f"{campaign_summary['risk_of_ruin']:.1f}%"],
        ["Best Parameter Set", campaign_summary["best_params"]],
    ]
    t_score = Table(scorecard_data, colWidths=[200, 340])
    t_score.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (1, 0), colors.HexColor("#EDF2F7")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
        ("PADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t_score)
    story.append(Spacer(1, 8))

    story.append(Paragraph("Top Strengths & Weaknesses", h1_style))
    story.append(Paragraph("<b>Top Strengths:</b>", bold_body))
    for str_item in campaign_summary["strengths"][:3]:
        story.append(Paragraph(f"- {str_item}", body_style))
    story.append(Spacer(1, 4))
    story.append(Paragraph("<b>Top Weaknesses:</b>", bold_body))
    for weak_item in campaign_summary["weaknesses"][:3]:
        story.append(Paragraph(f"- {weak_item}", body_style))

    story.append(Spacer(1, 8))
    story.append(Paragraph(f"<b>Recommended Next Action:</b> {campaign_summary['next_action']}", body_style))

    story.append(PageBreak())

    # Metadata & Details Page
    story.append(Paragraph("Campaign System & Validation Metadata", h1_style))
    meta_data = [
        [Paragraph("Metadata Field", bold_body), Paragraph("Value", bold_body)],
        ["Git Commit Hash", git_commit],
        ["Git Branch", git_branch],
        ["Python version", platform.python_version()],
        ["MetaTrader 5 Build", mt5_build],
        ["Seed", str(campaign_seed)],
        ["Dataset Hash", metadata["dataset_hash"]],
        ["Campaign Duration", f"{elapsed_time:.2f} seconds"],
    ]
    t_meta = Table(meta_data, colWidths=[200, 340])
    t_meta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (1, 0), colors.HexColor("#EDF2F7")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
        ("PADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(t_meta)

    # Plot Plateaus and curves if available
    stability_plot = get_artifacts_dir() / "stability_heatmap.png"
    if stability_plot.exists():
        story.append(Spacer(1, 8))
        story.append(Paragraph("Parameter Stability Plateau Heatmap", h1_style))
        story.append(Image(str(stability_plot), width=350, height=270))

    doc.build(story)

    # Copy files to artifacts/research_campaign/latest/
    shutil.copy(campaign_dir / "research_campaign.pdf", latest_dir / "research_campaign.pdf")
    shutil.copy(campaign_dir / "research_campaign.json", latest_dir / "research_campaign.json")
    shutil.copy(campaign_dir / "research_campaign.md", latest_dir / "research_campaign.md")
    shutil.copy(campaign_dir / "cross_market_summary.csv", latest_dir / "cross_market_summary.csv")
    shutil.copy(campaign_dir / "optimization_summary.csv", latest_dir / "optimization_summary.csv")
    shutil.copy(campaign_dir / "monte_carlo_summary.csv", latest_dir / "monte_carlo_summary.csv")
    shutil.copy(campaign_dir / "robustness_summary.csv", latest_dir / "robustness_summary.csv")
    shutil.copy(campaign_dir / "stability_summary.csv", latest_dir / "stability_summary.csv")

    # Clean resume state on success
    if state_path.exists():
        os.remove(state_path)

    logger.info("==================================================")
    logger.info("Campaign completed successfully. Saved to %s", campaign_dir)
    logger.info("==================================================")


if __name__ == "__main__":
    main()
