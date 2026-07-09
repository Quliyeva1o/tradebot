#!/usr/bin/env python3
"""Standard Backtest Runner for Reproducible Regression Testing."""

import argparse
import csv
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import yaml

# Add project root to python path to prevent import issues
sys.path.append(str(Path(__file__).parent.resolve()))

from application.services.market_state_builder import MarketStateBuilder
from backtest.engine import BacktestEngine
from backtest.models import BacktestConfig, BacktestResult
from backtest.report import BacktestReportGenerator
from backtest.report_models import PerformanceMetrics
from core.models import Bar, Timeframe
from data.csv_provider import CSVDataProvider
from mt5.history_downloader import MT5HistoryDownloader
from strategy.continuation import BearishContinuationStrategy, BullishContinuationStrategy
from strategy.strategy_engine import StrategyEngine
from utils.logging import setup_logger

logger = setup_logger("run_backtest")


def load_config(config_path: str) -> dict[str, Any]:
    """Loads configuration from a YAML file.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        A dictionary containing configurations.
    """
    logger.info("Loading configuration from %s", config_path)
    with open(config_path) as f:
        config: dict[str, Any] = yaml.safe_load(f)
    return config


def check_and_get_data(config: dict[str, Any]) -> list[Bar]:
    """Checks if the data file exists, downloading it via MT5 if necessary.

    Args:
        config: Loaded configuration dictionary.

    Returns:
        A list of Bar objects loaded from the CSV file.
    """
    symbol = config["symbol"]
    tf_str = config["timeframe"]

    # Handle datetime parsing from config
    start_dt = config["start_date"]
    end_dt = config["end_date"]
    if isinstance(start_dt, str):
        start_dt = datetime.strptime(start_dt, "%Y-%m-%d %H:%M:%S")
    if isinstance(end_dt, str):
        end_dt = datetime.strptime(end_dt, "%Y-%m-%d %H:%M:%S")

    # Target path format: data/history/{symbol}_{timeframe}.csv
    export_dir = Path("data/history")
    file_name = f"{symbol}_{tf_str}.csv"
    csv_path = export_dir / file_name

    fallback_path = Path(f"data/{symbol}_{tf_str}.csv")

    # If file doesn't exist, attempt download from MT5
    if not csv_path.exists():
        logger.info("Target data file %s not found in data/history. Invoking MT5 downloader...", file_name)
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


def execute_backtest(
    config: dict[str, Any], candles: list[Bar]
) -> tuple[BacktestResult, PerformanceMetrics, float]:
    """Configures and executes the backtest simulation.

    Args:
        config: Configuration dictionary.
        candles: List of candlestick bars.

    Returns:
        A tuple of (BacktestResult, PerformanceMetrics, elapsed_time).
    """
    logger.info("Configuring backtest engine and strategy...")

    tf_str = config["timeframe"]
    try:
        timeframe_enum = Timeframe[tf_str]
    except KeyError:
        timeframe_enum = Timeframe.H1

    # 1. State Builder
    state_builder = MarketStateBuilder(symbol=config["symbol"], timeframe=timeframe_enum)

    # 2. Strategy Engine
    strategy_engine = StrategyEngine()
    strategy_engine.register_strategy(BullishContinuationStrategy(min_risk_reward_ratio=1.0))
    strategy_engine.register_strategy(BearishContinuationStrategy(min_risk_reward_ratio=1.0))

    # 3. Backtest Config
    backtest_config = BacktestConfig(
        initial_balance=float(config["initial_balance"]),
        risk_per_trade=float(config["risk_per_trade"]),
        spread=float(config["spread"]),
        commission=float(config["commission"]),
        slippage=float(config["slippage"]),
        max_holding_bars=config.get("max_holding_bars"),
        commission_per_lot=config.get("commission_per_lot"),
        max_daily_loss_pct=config.get("max_daily_loss_pct"),
        max_equity_drawdown_pct=config.get("max_equity_drawdown_pct"),
    )

    # 4. Engine
    engine = BacktestEngine(config=backtest_config)

    # 5. Run simulation
    start_time = time.perf_counter()
    result = engine.run(candles, strategy_engine, state_builder)
    elapsed_time = time.perf_counter() - start_time

    # 5b. Strategy rejection diagnostics (FAZA 3.5)
    diagnostics = strategy_engine.get_diagnostics()
    if diagnostics:
        logger.info("Strategy rejection diagnostics: %s", json.dumps(diagnostics, indent=2))

    # 6. Report generation
    report_gen = BacktestReportGenerator()
    metrics = report_gen.generate(result)

    return result, metrics, elapsed_time


def generate_pdf(
    pdf_path: Path,
    config: dict[str, Any],
    result: BacktestResult,
    metrics: PerformanceMetrics,
    elapsed_time: float,
    compare_metrics: dict[str, Any] | None,
    regression_status: str,
    png_path: Path,
) -> None:
    """Generates a professional PDF report using ReportLab.

    Args:
        pdf_path: Target path for the PDF file.
        config: Configuration dictionary.
        result: Simulated execution results.
        metrics: Extended performance metrics.
        elapsed_time: Backtest execution duration.
        compare_metrics: Loaded baseline metrics for comparison.
        regression_status: Overall status string.
        png_path: Path to the generated equity curve plot.
    """
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

    winners = sorted([t for t in result.trades if t.pnl > 0], key=lambda x: x.pnl, reverse=True)
    losers = sorted([t for t in result.trades if t.pnl < 0], key=lambda x: abs(x.pnl), reverse=True)

    doc = SimpleDocTemplate(
        str(pdf_path),
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
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#1A365D"),
        spaceAfter=15,
        alignment=0,
    )
    h1_style = ParagraphStyle(
        "Heading1",
        parent=styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#2B6CB0"),
        spaceBefore=12,
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
    bold_body = ParagraphStyle(
        "BoldBody",
        parent=body_style,
        fontName="Helvetica-Bold",
    )
    note_style = ParagraphStyle(
        "Note",
        parent=body_style,
        fontSize=7,
        textColor=colors.HexColor("#718096"),
        fontName="Helvetica-Oblique",
    )

    # 1. Header / Title
    story.append(Paragraph("Backtest Performance & Research Summary", title_style))
    story.append(Spacer(1, 5))

    # 2. Executive Summary
    story.append(Paragraph("Executive Summary", h1_style))
    p_text = (
        f"This report presents a professional, institutional-grade performance and risk analysis of the continuation strategy on "
        f"<b>{config['symbol']}</b> on the <b>{config['timeframe']}</b> timeframe. The backtest simulated execution over the period "
        f"{config['start_date']} to {config['end_date']}. Total realized net profit was <b>{metrics.net_profit:+.2f}</b> "
        f"({(metrics.net_profit / config['initial_balance']) * 100:+.2f}% return) over <b>{metrics.total_trades}</b> executed trades. "
        f"The overall regression verification status has been evaluated as <b>{regression_status}</b>."
    )
    story.append(Paragraph(p_text, body_style))
    story.append(Spacer(1, 8))

    # 3. Configuration Table
    story.append(Paragraph("Configuration Settings", h1_style))
    config_data = [
        [Paragraph("Parameter", bold_body), Paragraph("Value", bold_body)],
        ["Symbol", config["symbol"]],
        ["Timeframe", config["timeframe"]],
        ["Start Date", str(config["start_date"])],
        ["End Date", str(config["end_date"])],
        ["Initial Balance", f"${config['initial_balance']:.2f}"],
        ["Risk Per Trade", f"{config['risk_per_trade'] * 100:.2f}%"],
        ["Spread", f"{config['spread']:.5f}"],
        ["Slippage", f"{config['slippage']:.5f}"],
        ["Commission", f"${config['commission']:.2f}"],
        ["Commission Per Lot", f"${config['commission_per_lot']}" if config.get("commission_per_lot") is not None else "None"],
    ]
    t_config = Table(config_data, colWidths=[200, 340])
    t_config.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (1, 0), colors.HexColor("#EDF2F7")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                ("PADDING", (0, 0), (-1, -1), 3),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(t_config)
    story.append(Spacer(1, 8))

    # 4. Performance Metrics
    story.append(Paragraph("Performance Metrics", h1_style))
    perf_data = [
        [Paragraph("Metric", bold_body), Paragraph("Value", bold_body), Paragraph("Metric", bold_body), Paragraph("Value", bold_body)],
        ["Total Trades", str(metrics.total_trades), "Win Rate", f"{metrics.win_rate * 100:.2f}%"],
        ["Winning Trades", str(metrics.winning_trades), "Losing Trades", str(metrics.losing_trades)],
        ["Expired Trades", str(metrics.expired_trades), "Profit Factor", f"{metrics.profit_factor:.4f}"],
        ["Gross Profit", f"${metrics.gross_profit:.2f}", "Gross Loss", f"${metrics.gross_loss:.2f}"],
        ["Net Profit", f"${metrics.net_profit:.2f}", "Expectancy", f"{metrics.expectancy:.4f}"],
        ["Average R Multiple", f"{metrics.average_r:.4f}", "Median R Multiple", f"{metrics.median_r:.4f}"],
        ["Largest Win", f"${metrics.largest_win:.2f}", "Largest Loss", f"${metrics.largest_loss:.2f}"],
        ["Average Win", f"${metrics.average_win:.2f}", "Average Loss", f"${metrics.average_loss:.2f}"],
    ]
    t_perf = Table(perf_data, colWidths=[140, 130, 140, 130])
    t_perf.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                ("PADDING", (0, 0), (-1, -1), 3),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(t_perf)

    story.append(PageBreak())

    # 5. Risk Analysis
    story.append(Paragraph("Risk & Drawdown Analysis", h1_style))
    dd_duration_str = str(timedelta(seconds=metrics.longest_drawdown_duration))
    rec_time_str = str(timedelta(seconds=metrics.recovery_time)) if metrics.recovery_time > 0 else "N/A"
    risk_data = [
        [Paragraph("Risk Metric", bold_body), Paragraph("Value", bold_body)],
        ["Max Drawdown", f"{metrics.max_drawdown * 100:.2f}%"],
        ["Average Drawdown", f"{metrics.average_drawdown * 100:.2f}%"],
        ["Longest Drawdown Duration", dd_duration_str],
        ["Average Recovery Time", rec_time_str],
        ["Sharpe Ratio", f"{metrics.sharpe_ratio:.4f}" if metrics.sharpe_ratio is not None else "N/A"],
        ["Sortino Ratio", f"{metrics.sortino_ratio:.4f}" if metrics.sortino_ratio is not None else "N/A"],
        ["Calmar Ratio", f"{metrics.calmar_ratio:.4f}" if metrics.calmar_ratio is not None else "N/A"],
    ]
    t_risk = Table(risk_data, colWidths=[200, 340])
    t_risk.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (1, 0), colors.HexColor("#EDF2F7")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                ("PADDING", (0, 0), (-1, -1), 3),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(t_risk)
    story.append(Spacer(1, 8))

    # 6. Trade Distribution
    story.append(Paragraph("Trade Distribution Statistics", h1_style))
    dist_data = [
        [Paragraph("Parameter", bold_body), Paragraph("Value", bold_body)],
        ["BUY Trades", str(metrics.buy_trades)],
        ["SELL Trades", str(metrics.sell_trades)],
        ["TP Exits", str(metrics.tp_exits)],
        ["SL Exits", str(metrics.sl_exits)],
        ["Expired Exits", str(metrics.expired_exits)],
        ["Average Risk-Reward Ratio (RR)", f"{metrics.average_rr:.2f}"],
        ["Median Risk-Reward Ratio (RR)", f"{metrics.median_rr:.2f}"],
        ["Largest Winning Streak", str(metrics.largest_winning_streak)],
        ["Largest Losing Streak", str(metrics.largest_losing_streak)],
        ["Average Holding Bars", f"{metrics.average_holding_bars:.2f}"],
        ["Median Holding Bars", f"{metrics.median_holding_bars:.2f}"],
        ["Average Winner Duration", str(timedelta(seconds=metrics.average_winner_duration))],
        ["Average Loser Duration", str(timedelta(seconds=metrics.average_loser_duration))],
    ]
    t_dist = Table(dist_data, colWidths=[200, 340])
    t_dist.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (1, 0), colors.HexColor("#EDF2F7")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                ("PADDING", (0, 0), (-1, -1), 3),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(t_dist)

    story.append(PageBreak())

    # 7. Monthly Table
    story.append(Paragraph("Monthly Performance", h1_style))
    monthly_data = [
        [
            Paragraph("Month", bold_body),
            Paragraph("Trades", bold_body),
            Paragraph("Win Rate", bold_body),
            Paragraph("Net Profit", bold_body),
            Paragraph("Profit Factor", bold_body),
            Paragraph("Expectancy", bold_body),
            Paragraph("Avg R", bold_body),
            Paragraph("Largest Win", bold_body),
            Paragraph("Largest Loss", bold_body),
            Paragraph("Max DD", bold_body),
        ]
    ]
    if metrics.monthly_statistics:
        for m in metrics.monthly_statistics:
            monthly_data.append(
                [
                    m["Month"],
                    str(m["Trades"]),
                    f"{m['Win Rate'] * 100:.1f}%",
                    f"${m['Net Profit']:.2f}",
                    f"{m['Profit Factor']:.2f}",
                    f"{m['Expectancy']:.2f}",
                    f"{m['Average R']:.2f}",
                    f"${m['Largest Win']:.2f}",
                    f"${m['Largest Loss']:.2f}",
                    f"{m['Drawdown'] * 100:.1f}%",
                ]
            )
    t_monthly = Table(
        monthly_data, colWidths=[60, 45, 55, 60, 50, 55, 50, 60, 60, 45]
    )
    t_monthly.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                ("PADDING", (0, 0), (-1, -1), 3),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    story.append(t_monthly)
    story.append(Spacer(1, 8))

    # 8. Equity Curve
    story.append(Paragraph("Equity Curve Plot", h1_style))
    story.append(Image(str(png_path), width=450, height=200))
    story.append(
        Paragraph(
            "Note: Equity equals Balance because the backtest engine calculates trade outcomes upon bar closure and does not compute floating PnL mid-bar.",
            note_style,
        )
    )
    story.append(Spacer(1, 8))

    # 9. Regression Summary
    story.append(Paragraph("Regression Summary", h1_style))
    if compare_metrics:
        trade_diff = int(metrics.total_trades) - compare_metrics.get("total_trades", 0)
        win_rate_diff = float(metrics.win_rate) - compare_metrics.get("win_rate", 0.0)
        net_profit_diff = float(metrics.net_profit) - compare_metrics.get("net_profit", 0.0)
        pf_diff = float(metrics.profit_factor) - compare_metrics.get("profit_factor", 1.0)
        expectancy_diff = float(metrics.expectancy) - compare_metrics.get("expectancy", 0.0)
        drawdown_diff = float(metrics.max_drawdown) - compare_metrics.get("max_drawdown", 0.0)

        reg_data = [
            [Paragraph("Metric", bold_body), Paragraph("Current", bold_body), Paragraph("Baseline", bold_body), Paragraph("Difference", bold_body)],
            ["Trades", str(metrics.total_trades), str(compare_metrics.get("total_trades", 0)), f"{trade_diff:+}"] ,
            ["Win Rate", f"{metrics.win_rate * 100:.2f}%", f"{compare_metrics.get('win_rate', 0.0) * 100:.2f}%", f"{win_rate_diff * 100:+.2f}%"],
            ["Net Profit", f"${metrics.net_profit:.2f}", f"${compare_metrics.get('net_profit', 0.0):.2f}", f"${net_profit_diff:+.2f}"],
            ["Profit Factor", f"{metrics.profit_factor:.4f}", f"{compare_metrics.get('profit_factor', 1.0):.4f}", f"{pf_diff:+.4f}"],
            ["Expectancy", f"{metrics.expectancy:.4f}", f"{compare_metrics.get('expectancy', 0.0):.4f}", f"{expectancy_diff:+.4f}"],
            ["Max Drawdown", f"{metrics.max_drawdown * 100:.2f}%", f"{compare_metrics.get('max_drawdown', 0.0) * 100:.2f}%", f"{drawdown_diff * 100:+.2f}%"],
        ]
        t_reg = Table(reg_data, colWidths=[150, 110, 110, 170])
        t_reg.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                    ("PADDING", (0, 0), (-1, -1), 3),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        story.append(t_reg)
        story.append(Spacer(1, 4))
        story.append(Paragraph(f"Overall Status: <b>{regression_status}</b>", body_style))
    else:
        story.append(Paragraph("Baseline Comparison: None (Initial Baseline saved)", body_style))
    story.append(Spacer(1, 8))

    # 10. Largest Winners & Losers
    story.append(Paragraph("Largest Winning Trades", h1_style))
    win_headers = [Paragraph("Rank", bold_body), Paragraph("Entry Time", bold_body), Paragraph("Exit Time", bold_body), Paragraph("Direction", bold_body), Paragraph("PnL", bold_body), Paragraph("R", bold_body)]
    win_table_data = [win_headers]
    for rk, tw in enumerate(winners[:3], 1):
        win_table_data.append([
            str(rk),
            tw.entry_time.strftime("%Y-%m-%d %H:%M"),
            tw.exit_time.strftime("%Y-%m-%d %H:%M"),
            tw.direction.name,
            f"${tw.pnl:.2f}",
            f"{tw.r_multiple:.2f}",
        ])
    t_winners = Table(win_table_data, colWidths=[40, 110, 110, 80, 100, 100])
    t_winners.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#E6FFFA")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('PADDING', (0,0), (-1,-1), 3),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(t_winners)
    story.append(Spacer(1, 8))

    story.append(Paragraph("Largest Losing Trades", h1_style))
    loss_headers = [Paragraph("Rank", bold_body), Paragraph("Entry Time", bold_body), Paragraph("Exit Time", bold_body), Paragraph("Direction", bold_body), Paragraph("PnL", bold_body), Paragraph("R", bold_body)]
    loss_table_data = [loss_headers]
    for rk, tl in enumerate(losers[:3], 1):
        loss_table_data.append([
            str(rk),
            tl.entry_time.strftime("%Y-%m-%d %H:%M"),
            tl.exit_time.strftime("%Y-%m-%d %H:%M"),
            tl.direction.name,
            f"-${abs(tl.pnl):.2f}",
            f"{tl.r_multiple:.2f}",
        ])
    t_losers = Table(loss_table_data, colWidths=[40, 110, 110, 80, 100, 100])
    t_losers.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#FFF5F5")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('PADDING', (0,0), (-1,-1), 3),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(t_losers)
    story.append(Spacer(1, 8))

    # 11. Conclusions
    story.append(Paragraph("Final Conclusions", h1_style))
    story.append(
        Paragraph(
            f"The backtest simulation finished successfully. All checks validated. The regression status has been evaluated as <b>{regression_status}</b>.",
            body_style,
        )
    )

    doc.build(story)


def write_reports(
    config: dict[str, Any],
    result: BacktestResult,
    metrics: PerformanceMetrics,
    elapsed_time: float,
    compare_metrics: dict[str, Any] | None = None,
) -> None:
    """Generates the required reports and files: report.md, metrics.json, trades.csv, run_metadata.json."""
    artifacts_dir = Path("artifacts")
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # 1. Save trades.csv
    trades_path = artifacts_dir / "trades.csv"
    with open(trades_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "entry_time",
                "exit_time",
                "direction",
                "entry_price",
                "exit_price",
                "stop_loss",
                "take_profit",
                "result",
                "pnl",
                "r_multiple",
                "symbol",
                "strategy_name",
                "trigger_reason",
                "confidence_score",
            ]
        )
        for t in result.trades:
            writer.writerow(
                [
                    t.entry_time,
                    t.exit_time,
                    t.direction.name,
                    f"{t.entry_price:.5f}",
                    f"{t.exit_price:.5f}",
                    f"{t.stop_loss:.5f}",
                    f"{t.take_profit:.5f}",
                    t.result.value,
                    f"{t.pnl:.2f}",
                    f"{t.r_multiple:.2f}",
                    t.symbol,
                    t.strategy_name,
                    t.trigger_reason,
                    f"{t.confidence_score:.2f}",
                ]
            )
    logger.info("Saved trade log to %s", trades_path)

    # 2. Save trade_distribution.csv
    dist_path = artifacts_dir / "trade_distribution.csv"
    with open(dist_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "Direction",
                "Exit Type",
                "PnL",
                "R",
                "Holding Bars",
                "Duration",
                "Position Size",
                "Spread Cost",
                "Commission",
                "Slippage",
            ]
        )
        for t in result.trades:
            # Spread Cost = (entry_spread + exit_spread) * position_size
            spread_cost = (t.entry_spread + t.exit_spread) * t.position_size
            # Recalculate commission
            comm = (
                config["commission_per_lot"] * t.position_size
                if config.get("commission_per_lot") is not None
                else config["commission"]
            )
            # Slippage Cost = 2 * slippage * position_size
            slip_cost = 2 * config["slippage"] * t.position_size

            writer.writerow(
                [
                    t.direction.name,
                    t.result.value,
                    f"{t.pnl:.2f}",
                    f"{t.r_multiple:.4f}",
                    str(t.bars_held),
                    f"{t.trade_duration:.2f}",
                    f"{t.position_size:.4f}",
                    f"{spread_cost:.5f}",
                    f"{comm:.2f}",
                    f"{slip_cost:.5f}",
                ]
            )
    logger.info("Saved trade distribution CSV to %s", dist_path)

    # 3. Save equity_curve.csv & plot equity_curve.png
    equity_path = artifacts_dir / "equity_curve.csv"
    trade_numbers = [0]
    balances = [result.initial_balance]
    equities = [result.initial_balance]
    drawdowns_pct = [0.0]

    peak_balance = result.initial_balance
    curr_balance = result.initial_balance

    for i, t in enumerate(result.trades, 1):
        curr_balance += t.pnl
        peak_balance = max(peak_balance, curr_balance)
        dd = (peak_balance - curr_balance) / peak_balance if peak_balance > 0 else 0.0

        trade_numbers.append(i)
        balances.append(curr_balance)
        equities.append(curr_balance)  # Equity = Balance as floating is not tracked mid-bar
        drawdowns_pct.append(dd * 100)

    with open(equity_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Trade Number", "Balance", "Equity", "Drawdown %"])
        for idx in range(len(trade_numbers)):
            writer.writerow(
                [
                    trade_numbers[idx],
                    f"{balances[idx]:.2f}",
                    f"{equities[idx]:.2f}",
                    f"{drawdowns_pct[idx]:.2f}",
                ]
            )
    logger.info("Saved equity curve CSV to %s", equity_path)

    # Plot png
    png_path = artifacts_dir / "equity_curve.png"
    fig, ax = plt.subplots(figsize=(8, 4), dpi=300)
    ax.plot(trade_numbers, balances, label="Realized Balance", color="#1F77B4", linewidth=1.5)
    # Since Equity = Balance, let's overlay or just plot balance and add a text
    ax.set_title("Realized Balance & Account Equity Curve", fontsize=11, fontweight="bold", color="#1A365D")
    ax.set_xlabel("Trade Number", fontsize=9, color="#2D3748")
    ax.set_ylabel("Balance / Equity ($)", fontsize=9, color="#2D3748")
    ax.grid(True, linestyle="--", alpha=0.5)
    ax.legend(loc="upper left", fontsize=8)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")
    # Footnote about limitation
    fig.text(
        0.1,
        0.01,
        "Limitation Note: Equity equals Balance as the backtester operates on closed bars and lacks mid-bar floating PnL tracking.",
        fontsize=6,
        color="#718096",
        style="italic",
    )
    plt.tight_layout()
    plt.savefig(png_path)
    plt.close()
    logger.info("Saved equity curve PNG plot to %s", png_path)

    # 4. Save monthly_performance.csv
    monthly_csv_path = artifacts_dir / "monthly_performance.csv"
    with open(monthly_csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "Month",
                "Trades",
                "Win Rate",
                "Net Profit",
                "Profit Factor",
                "Expectancy",
                "Average R",
                "Largest Win",
                "Largest Loss",
            ]
        )
        if metrics.monthly_statistics:
            for m in metrics.monthly_statistics:
                writer.writerow(
                    [
                        m["Month"],
                        str(m["Trades"]),
                        f"{m['Win Rate'] * 100:.2f}%",
                        f"{m['Net Profit']:.2f}",
                        f"{m['Profit Factor']:.4f}",
                        f"{m['Expectancy']:.4f}",
                        f"{m['Average R']:.4f}",
                        f"{m['Largest Win']:.2f}",
                        f"{m['Largest Loss']:.2f}",
                    ]
                )
    logger.info("Saved monthly performance CSV to %s", monthly_csv_path)

    # 5. Evaluate regression status
    regression_status = "PASS"
    if compare_metrics:
        net_profit_diff = float(metrics.net_profit) - compare_metrics.get("net_profit", 0.0)
        drawdown_diff = float(metrics.max_drawdown) - compare_metrics.get("max_drawdown", 0.0)

        # Failure threshold: net profit collapsed by 10% of initial balance, or drawdown increased by more than 10%
        if net_profit_diff < -0.10 * float(config["initial_balance"]) or drawdown_diff > 0.10:
            regression_status = "FAIL"
        # Warning threshold: net profit decreased or drawdown increased slightly
        elif net_profit_diff < 0.0 or drawdown_diff > 0.02:
            regression_status = "PASS WITH WARNINGS"

    # 6. Save backtest_metrics.json
    metrics_path = artifacts_dir / "backtest_metrics.json"
    metrics_dict = {
        "total_trades": int(metrics.total_trades),
        "winning_trades": int(metrics.winning_trades),
        "losing_trades": int(metrics.losing_trades),
        "expired_trades": int(metrics.expired_trades),
        "win_rate": float(metrics.win_rate),
        "loss_rate": float(metrics.loss_rate),
        "profit_factor": float(metrics.profit_factor),
        "expectancy": float(metrics.expectancy),
        "average_r": float(metrics.average_r),
        "median_r": float(metrics.median_r),
        "average_win": float(metrics.average_win),
        "average_loss": float(metrics.average_loss),
        "largest_win": float(metrics.largest_win),
        "largest_loss": float(metrics.largest_loss),
        "gross_profit": float(metrics.gross_profit),
        "gross_loss": float(metrics.gross_loss),
        "net_profit": float(metrics.net_profit),
        "max_drawdown": float(metrics.max_drawdown),
        "average_drawdown": float(metrics.average_drawdown),
        "longest_drawdown_duration": float(metrics.longest_drawdown_duration),
        "recovery_time": float(metrics.recovery_time),
        "drawdown_distribution": metrics.drawdown_distribution,
        "sharpe_ratio": float(metrics.sharpe_ratio) if metrics.sharpe_ratio is not None else None,
        "sortino_ratio": float(metrics.sortino_ratio) if metrics.sortino_ratio is not None else None,
        "calmar_ratio": float(metrics.calmar_ratio) if metrics.calmar_ratio is not None else None,
        "buy_trades": int(metrics.buy_trades),
        "sell_trades": int(metrics.sell_trades),
        "tp_exits": int(metrics.tp_exits),
        "sl_exits": int(metrics.sl_exits),
        "expired_exits": int(metrics.expired_exits),
        "average_rr": float(metrics.average_rr),
        "median_rr": float(metrics.median_rr),
        "largest_winning_streak": int(metrics.largest_winning_streak),
        "largest_losing_streak": int(metrics.largest_losing_streak),
    }

    with open(metrics_path, "w") as f:
        json.dump(metrics_dict, f, indent=4)
    logger.info("Saved metrics JSON to %s", metrics_path)

    # 7. Save run_metadata.json
    metadata_path = artifacts_dir / "run_metadata.json"
    metadata_dict = {
        "symbol": config["symbol"],
        "timeframe": config["timeframe"],
        "start_date": str(config["start_date"]),
        "end_date": str(config["end_date"]),
        "execution_timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_time_seconds": elapsed_time,
        "initial_balance": float(config["initial_balance"]),
        "final_balance": float(result.final_balance),
        "total_trades": int(metrics.total_trades),
        "win_rate": float(metrics.win_rate),
        "stopped_early": bool(result.stopped_early),
        "stop_reason": result.stop_reason,
    }
    with open(metadata_path, "w") as f:
        json.dump(metadata_dict, f, indent=4)
    logger.info("Saved metadata JSON to %s", metadata_path)

    # 8. Save backtest_summary.pdf
    pdf_path = artifacts_dir / "backtest_summary.pdf"
    generate_pdf(
        pdf_path=pdf_path,
        config=config,
        result=result,
        metrics=metrics,
        elapsed_time=elapsed_time,
        compare_metrics=compare_metrics,
        regression_status=regression_status,
        png_path=png_path,
    )
    logger.info("Saved PDF summary report to %s", pdf_path)

    # 9. Generate backtest_report.md
    report_path = artifacts_dir / "backtest_report.md"

    # Sort largest winners and losers
    winners = sorted([t for t in result.trades if t.pnl > 0], key=lambda x: x.pnl, reverse=True)
    losers = sorted([t for t in result.trades if t.pnl < 0], key=lambda x: abs(x.pnl), reverse=True)

    markdown_content = f"""# Backtest Performance Report

## Configuration
- **Symbol**: {config['symbol']}
- **Timeframe**: {config['timeframe']}
- **Date Range**: {config['start_date']} to {config['end_date']}
- **Initial Balance**: ${config['initial_balance']:.2f}
- **Risk Per Trade**: {config['risk_per_trade'] * 100:.2f}%
- **Spread**: {config['spread']:.5f}
- **Slippage**: {config['slippage']:.5f}
- **Commission**: ${config['commission']:.2f}
- **Commission Per Lot**: ${config['commission_per_lot'] if config.get('commission_per_lot') is not None else 'None'}

---

## Performance
- **Total Trades**: {metrics.total_trades}
- **Win Rate**: {metrics.win_rate * 100:.2f}%
- **Gross Profit**: ${metrics.gross_profit:.2f}
- **Gross Loss**: ${metrics.gross_loss:.2f}
- **Net Profit**: ${metrics.net_profit:.2f}
- **Profit Factor**: {metrics.profit_factor:.4f}
- **Expectancy**: {metrics.expectancy:.4f}
- **Average R**: {metrics.average_r:.4f}
- **Median R**: {metrics.median_r:.4f}
- **Average Win**: ${metrics.average_win:.2f}
- **Average Loss**: ${metrics.average_loss:.2f}
- **Largest Win**: ${metrics.largest_win:.2f}
- **Largest Loss**: ${metrics.largest_loss:.2f}

---

## Risk
- **Max Drawdown**: {metrics.max_drawdown * 100:.2f}%
- **Average Drawdown**: {metrics.average_drawdown * 100:.2f}%
- **Longest Drawdown**: {timedelta(seconds=metrics.longest_drawdown_duration)!s}
- **Recovery Time**: {str(timedelta(seconds=metrics.recovery_time)) if metrics.recovery_time > 0 else 'N/A'}
- **Sharpe Ratio**: {f"{metrics.sharpe_ratio:.4f}" if metrics.sharpe_ratio is not None else 'N/A'}
- **Sortino Ratio**: {f"{metrics.sortino_ratio:.4f}" if metrics.sortino_ratio is not None else 'N/A'}
- **Calmar Ratio**: {f"{metrics.calmar_ratio:.4f}" if metrics.calmar_ratio is not None else 'N/A'}

---

## Trade Distribution
- **BUY Trades**: {metrics.buy_trades}
- **SELL Trades**: {metrics.sell_trades}
- **TP Exits**: {metrics.tp_exits}
- **SL Exits**: {metrics.sl_exits}
- **Expired Exits**: {metrics.expired_exits}
- **Average RR**: {metrics.average_rr:.2f}
- **Median RR**: {metrics.median_rr:.2f}
- **Streak Wins**: {metrics.largest_winning_streak}
- **Streak Losses**: {metrics.largest_losing_streak}

---

## Monthly Results
| Month | Trades | Win Rate | Net Profit | Profit Factor | Expectancy | Average R | Largest Win | Largest Loss | Drawdown |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
"""
    if metrics.monthly_statistics:
        for m in metrics.monthly_statistics:
            markdown_content += (
                f"| {m['Month']} | {m['Trades']} | {m['Win Rate'] * 100:.1f}% | "
                f"${m['Net Profit']:.2f} | {m['Profit Factor']:.2f} | {m['Expectancy']:.2f} | "
                f"{m['Average R']:.2f} | ${m['Largest Win']:.2f} | ${m['Largest Loss']:.2f} | "
                f"{m['Drawdown'] * 100:.1f}% |\n"
            )

    markdown_content += f"""
---

## Drawdown Analysis
- **Drawdown Distribution**:
  - 0-2%: {metrics.drawdown_distribution.get("0-2%", 0) if metrics.drawdown_distribution else 0} trade closures
  - 2-5%: {metrics.drawdown_distribution.get("2-5%", 0) if metrics.drawdown_distribution else 0} trade closures
  - 5-10%: {metrics.drawdown_distribution.get("5-10%", 0) if metrics.drawdown_distribution else 0} trade closures
  - 10%+: {metrics.drawdown_distribution.get("10%+", 0) if metrics.drawdown_distribution else 0} trade closures

---

## Regression Summary
"""
    if compare_metrics:
        trade_diff = int(metrics.total_trades) - compare_metrics.get("total_trades", 0)
        win_rate_diff = float(metrics.win_rate) - compare_metrics.get("win_rate", 0.0)
        net_profit_diff = float(metrics.net_profit) - compare_metrics.get("net_profit", 0.0)
        pf_diff = float(metrics.profit_factor) - compare_metrics.get("profit_factor", 1.0)
        expectancy_diff = float(metrics.expectancy) - compare_metrics.get("expectancy", 0.0)
        drawdown_diff = float(metrics.max_drawdown) - compare_metrics.get("max_drawdown", 0.0)

        markdown_content += f"""- **Baseline Compared Against**: `baseline_v1.json`
- **Trade Difference**: {trade_diff:+}
- **Win Rate Difference**: {win_rate_diff * 100:+.2f}%
- **Net Profit Difference**: {net_profit_diff:+.2f}
- **Profit Factor Difference**: {pf_diff:+.4f}
- **Expectancy Difference**: {expectancy_diff:+.4f}
- **Drawdown Difference**: {drawdown_diff * 100:+.2f}%
- **Overall Status**: **{regression_status}**
"""
    else:
        markdown_content += f"""- **Baseline Compared Against**: None (Initial Baseline saved)
- **Overall Status**: **{regression_status}**
"""

    markdown_content += "\n---\n\n## Largest Winners\n"
    for idx_w, tw in enumerate(winners[:3], 1):
        markdown_content += f"- **Trade {idx_w}**: Entry: {tw.entry_time.strftime('%Y-%m-%d %H:%M')}, Exit: {tw.exit_time.strftime('%Y-%m-%d %H:%M')}, PnL: ${tw.pnl:.2f}, R: {tw.r_multiple:.2f}\n"

    markdown_content += "\n---\n\n## Largest Losers\n"
    for idx_l, tl in enumerate(losers[:3], 1):
        markdown_content += f"- **Trade {idx_l}**: Entry: {tl.entry_time.strftime('%Y-%m-%d %H:%M')}, Exit: {tl.exit_time.strftime('%Y-%m-%d %H:%M')}, PnL: -${abs(tl.pnl):.2f}, R: {tl.r_multiple:.2f}\n"

    markdown_content += f"""
---

## Conclusions
- The backtesting engine successfully ran the simulation.
- Realized a net profit of ${metrics.net_profit:.2f}.
- The regression overall status is evaluated as **{regression_status}**.
"""

    with open(report_path, "w") as f:
        f.write(markdown_content)
    logger.info("Saved Markdown report to %s", report_path)


def save_baseline(metrics_dict: dict) -> None:
    """Saves the current metrics as the official baseline_v1.json."""
    baseline_dir = Path("artifacts/baselines")
    baseline_dir.mkdir(parents=True, exist_ok=True)
    baseline_path = baseline_dir / "baseline_v1.json"

    with open(baseline_path, "w") as f:
        json.dump(metrics_dict, f, indent=4)
    logger.info("Saved baseline metrics to %s", baseline_path)


def main() -> None:
    """Main backtest runner entrypoint."""
    parser = argparse.ArgumentParser(description="Modular SMC Backtest Runner")
    parser.add_argument(
        "--config",
        type=str,
        default="config/backtest.yaml",
        help="Path to the YAML backtest configuration file.",
    )
    parser.add_argument(
        "--compare",
        type=str,
        default=None,
        help="Path to a baseline JSON file to perform regression comparison.",
    )
    args = parser.parse_args()

    try:
        # 1. Load config
        config = load_config(args.config)

        # 2. Get data
        candles = check_and_get_data(config)

        # 3. Run backtest
        result, metrics, elapsed = execute_backtest(config, candles)

        # 4. Baseline comparison
        compare_metrics = None
        if args.compare:
            logger.info("Loading baseline comparison from %s", args.compare)
            with open(args.compare) as f:
                compare_metrics = json.load(f)

        # 5. Write reports
        write_reports(config, result, metrics, elapsed, compare_metrics)

        # 6. Save baseline if we are not comparing
        if not args.compare:
            # Re-read metrics JSON to ensure dictionary structure matches
            metrics_path = Path("artifacts/backtest_metrics.json")
            with open(metrics_path) as f:
                saved_metrics = json.load(f)
            save_baseline(saved_metrics)

    except Exception:
        logger.exception("Backtest runner encountered a fatal error:")
        sys.exit(1)


if __name__ == "__main__":
    main()
