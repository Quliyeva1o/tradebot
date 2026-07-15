"""Research Dashboard and PDF Reporting module."""

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

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

from utils.logging import setup_logger
from utils.paths import get_artifacts_dir

logger = setup_logger("dashboard")


def _format_entry_time(raw: str) -> str:
    """Formats a trades.csv timestamp string as YYYY-MM-DD HH:MM.

    Args:
        raw: The raw timestamp string as written by run_backtest.py (str(datetime)).

    Returns:
        The timestamp formatted as YYYY-MM-DD HH:MM, or the original string if unparseable.
    """
    try:
        return datetime.fromisoformat(raw).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return raw


class ResearchDashboard:
    """Aggregates validation module results, calculates safety/robustness scores, and compiles reports."""

    def __init__(self) -> None:
        """Initializes the ResearchDashboard."""
        pass

    def run(
        self,
        symbol: str,
        timeframe: str,
        results: dict[str, Any],
        trades_csv_path: str | Path | None = None,
        max_trade_log_rows: int = 50,
    ) -> None:
        """Compiles the aggregated results into Markdown and ReportLab PDF dashboard formats.

        Args:
            symbol: Trading instrument.
            timeframe: Timeframe of simulation.
            results: Dictionary containing outcomes and status logs from each modular step.
            trades_csv_path: Optional path to a trades.csv file (as written by
                run_backtest.py) to render as a Trade Log section. If None or the
                file doesn't exist, the Trade Log section is omitted.
            max_trade_log_rows: Maximum number of most recent trades to render when
                trades_csv_path is given. Defaults to 50.
        """
        logger.info("Assembling Research validation dashboard...")

        # Initialize default metrics
        wf_score = "N/A"
        opt_score = "N/A"
        mc_score = "N/A"
        rob_score = "N/A"
        risk_score = "N/A"
        overall_score_val = 0.0
        scores_added = 0

        # 1. Walk-Forward Score (percentage of profitable folds)
        if results.get("walk_forward", {}).get("status") == "SUCCESS":
            folds = results["walk_forward"].get("data", [])
            if folds:
                prof_folds = sum(1 for f in folds if f["val_net_profit"] > 0)
                wf_val = (prof_folds / len(folds)) * 100.0
                wf_score = f"{wf_val:.1f}/100"
                overall_score_val += wf_val
                scores_added += 1

        # 2. Optimization Score (relative PnL improvement vs baseline)
        if results.get("optimization", {}).get("status") == "SUCCESS":
            opt_data = results["optimization"].get("data", {})
            baseline_pnl = results.get("robustness", {}).get("data", {}).get("baseline", {}).get("net_profit", 1.0)
            best_pnl = opt_data.get("best_pnl", 0.0)
            if best_pnl > 0:
                imp = ((best_pnl - baseline_pnl) / max(1.0, abs(baseline_pnl))) * 100.0
                opt_val = min(100.0, max(0.0, 50.0 + imp))
                opt_score = f"{opt_val:.1f}/100"
                overall_score_val += opt_val
                scores_added += 1

        # 3. Monte Carlo Score (based on risk of ruin)
        if results.get("monte_carlo", {}).get("status") == "SUCCESS":
            mc_data = results["monte_carlo"].get("data", {})
            ruin = mc_data.get("risk_of_ruin", 100.0)
            mc_val = max(0.0, 100.0 - ruin * 2.0)  # Heavy penalty for high ruin
            mc_score = f"{mc_val:.1f}/100"
            overall_score_val += mc_val
            scores_added += 1

        # 4. Robustness Score (fraction of profitable stress tests)
        if results.get("robustness", {}).get("status") == "SUCCESS":
            rob_data = results["robustness"].get("data", {})
            scenarios = ["high_spread", "high_commission", "high_slippage", "skipped_10pct", "skipped_25pct"]
            prof_scenarios = 0
            for sc in scenarios:
                if rob_data.get(sc, {}).get("net_profit", -1.0) > 0:
                    prof_scenarios += 1
            rob_val = (prof_scenarios / len(scenarios)) * 100.0
            rob_score = f"{rob_val:.1f}/100"
            overall_score_val += rob_val
            scores_added += 1

            # 5. Risk Score (using baseline drawdown)
            base_dd = rob_data.get("baseline", {}).get("max_drawdown", 1.0)
            risk_val = max(0.0, (1.0 - base_dd) * 100.0)
            risk_score = f"{risk_val:.1f}/100"
            overall_score_val += risk_val
            scores_added += 1

        overall_score = "N/A"
        rec = "NOT ROBUST"
        if scores_added > 0:
            avg_score = overall_score_val / scores_added
            overall_score = f"{avg_score:.1f}/100"

            # Determine Recommendation
            # Default risk check
            risk_val = float(risk_score.split("/")[0]) if risk_score != "N/A" else 0.0
            if avg_score >= 80.0 and risk_val >= 80.0:
                rec = "READY FOR LIVE"
            elif avg_score >= 50.0:
                rec = "NEEDS IMPROVEMENT"
            else:
                rec = "NOT ROBUST"

        scores = {
            "overall": overall_score,
            "risk": risk_score,
            "robustness": rob_score,
            "optimization": opt_score,
            "walk_forward": wf_score,
            "monte_carlo": mc_score,
            "recommendation": rec,
        }

        trade_rows, total_trade_count = self._load_trade_log(trades_csv_path, max_trade_log_rows)

        # Single-backtest mode: a Trade Log was supplied but none of the validation
        # suite modules ran, so a Quality Scorecard / "NOT ROBUST" recommendation
        # would misleadingly imply a full validation suite was attempted.
        single_backtest_mode = bool(trade_rows) and not any(
            results.get(module) for module in ("walk_forward", "optimization", "monte_carlo", "robustness")
        )

        # 1. Export research_dashboard.md
        self._export_markdown(
            symbol, timeframe, results, scores, trade_rows, total_trade_count, max_trade_log_rows, single_backtest_mode
        )

        # 2. Export research_summary.pdf
        self._export_pdf(
            symbol, timeframe, results, scores, trade_rows, total_trade_count, max_trade_log_rows, single_backtest_mode
        )

    def _load_trade_log(
        self, trades_csv_path: str | Path | None, max_rows: int
    ) -> tuple[list[dict[str, str]], int]:
        """Reads trade rows from a trades.csv file, if provided.

        Args:
            trades_csv_path: Path to a trades.csv file (as written by run_backtest.py), or None.
            max_rows: Maximum number of most recent trades to keep for display.

        Returns:
            A tuple of (rows to display, total trade count in the file). Empty/(0)
            if trades_csv_path is None, missing, or has no trade rows.
        """
        if trades_csv_path is None:
            return [], 0

        csv_path = Path(trades_csv_path)
        if not csv_path.exists():
            logger.warning("Trade log CSV not found at %s; skipping Trade Log section.", csv_path)
            return [], 0

        with open(csv_path, newline="") as f:
            rows = list(csv.DictReader(f))

        if not rows:
            return [], 0

        display_rows = rows[-max_rows:] if len(rows) > max_rows else rows
        return display_rows, len(rows)

    def _export_markdown(
        self,
        symbol: str,
        timeframe: str,
        results: dict[str, Any],
        scores: dict[str, Any],
        trade_rows: list[dict[str, str]],
        total_trade_count: int,
        max_trade_log_rows: int,
        single_backtest_mode: bool,
    ) -> None:
        """Exports MD research dashboard."""
        artifacts_dir = get_artifacts_dir()
        md_path = artifacts_dir / "research_dashboard.md"

        if single_backtest_mode:
            md = f"""# Single-Backtest Trade Log Report

- **Instrument**: {symbol}
- **Timeframe**: {timeframe}

This report covers a single backtest run with trade-level detail. The full
validation suite (Walk Forward, Optimization, Monte Carlo, Robustness) was
not run, so no Quality Scorecard or recommendation is shown.
"""
        else:
            md = f"""# Research & Validation Dashboard

- **Instrument**: {symbol}
- **Timeframe**: {timeframe}
- **Validation Recommendation**: **{scores['recommendation']}**

---

## Score Card
- **Overall Quality Score**: {scores['overall']}
- **Risk Score**: {scores['risk']}
- **Robustness Score**: {scores['robustness']}
- **Optimization Stability Score**: {scores['optimization']}
- **Walk Forward Score**: {scores['walk_forward']}
- **Monte Carlo Score**: {scores['monte_carlo']}

---

## Module Statuses

| Validation Module | Status | Details / Diagnostic |
| --- | --- | --- |
| **Walk Forward** | {results.get('walk_forward', {}).get('status', 'NOT RUN')} | {results.get('walk_forward', {}).get('message', '-')} |
| **Optimization** | {results.get('optimization', {}).get('status', 'NOT RUN')} | {results.get('optimization', {}).get('message', '-')} |
| **Monte Carlo** | {results.get('monte_carlo', {}).get('status', 'NOT RUN')} | {results.get('monte_carlo', {}).get('message', '-')} |
| **Robustness** | {results.get('robustness', {}).get('status', 'NOT RUN')} | {results.get('robustness', {}).get('message', '-')} |
| **Stability** | {results.get('stability', {}).get('status', 'NOT RUN')} | {results.get('stability', {}).get('message', '-')} |

---

## Concluding Comments
- Dashboard aggregated successfully. Recommendation set to **{scores['recommendation']}**.
"""
        if trade_rows:
            md += self._render_trade_log_markdown(trade_rows, total_trade_count, max_trade_log_rows)

        with open(md_path, "w") as f:
            f.write(md)
        logger.info("Saved research MD dashboard to %s", md_path)

    def _render_trade_log_markdown(
        self, trade_rows: list[dict[str, str]], total_trade_count: int, max_rows: int
    ) -> str:
        """Renders the Trade Log section as a Markdown table.

        Args:
            trade_rows: The (possibly truncated) trade rows to render.
            total_trade_count: The total number of trades in the source CSV.
            max_rows: The configured row cap, used to decide whether to show the truncation note.

        Returns:
            A Markdown string for the Trade Log section.
        """
        lines = ["\n---\n\n## Trade Log\n"]
        if total_trade_count > max_rows:
            lines.append(
                f"Showing the last {len(trade_rows)} of {total_trade_count} trades. "
                "See `trades.csv` in the artifacts directory for the full list.\n"
            )
        lines.append("| Entry Time | Direction | Entry Price | Exit Price | Result | P&L |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for row in trade_rows:
            entry_time = _format_entry_time(row.get("entry_time", ""))
            lines.append(
                f"| {entry_time} | {row.get('direction', '-')} | {row.get('entry_price', '-')} | "
                f"{row.get('exit_price', '-')} | {row.get('result', '-')} | ${row.get('pnl', '0.00')} |"
            )
        return "\n".join(lines) + "\n"

    def _export_pdf(
        self,
        symbol: str,
        timeframe: str,
        results: dict[str, Any],
        scores: dict[str, Any],
        trade_rows: list[dict[str, str]],
        total_trade_count: int,
        max_trade_log_rows: int,
        single_backtest_mode: bool,
    ) -> None:
        """Exports PDF summary using ReportLab."""
        artifacts_dir = get_artifacts_dir()
        pdf_path = artifacts_dir / "research_summary.pdf"

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

        if single_backtest_mode:
            story.append(Paragraph("Single-Backtest Trade Log Report", title_style))
            story.append(Spacer(1, 5))
            p_text = (
                f"This report covers a single backtest run for symbol <b>{symbol}</b> on timeframe "
                f"<b>{timeframe}</b> with trade-level detail. The full validation suite (Walk Forward, "
                f"Optimization, Monte Carlo, Robustness) was not run, so no Quality Scorecard or "
                f"recommendation is shown."
            )
            story.append(Paragraph(p_text, body_style))
            story.append(Spacer(1, 8))
        else:
            story.append(Paragraph("Validation & Research Summary Dashboard", title_style))
            story.append(Spacer(1, 5))

            # Executive Summary
            story.append(Paragraph("Executive Summary", h1_style))
            p_text = (
                f"This summary aggregates validation metrics for symbol <b>{symbol}</b> on timeframe <b>{timeframe}</b>. "
                f"Following walk-forward testing, random execution cost disturbances, sequence bootstrapping, and sensitivity plateaus, "
                f"the validation suite assigns a recommendation status of <b>{scores['recommendation']}</b>."
            )
            story.append(Paragraph(p_text, body_style))
            story.append(Spacer(1, 8))

            # Scorecard table
            story.append(Paragraph("Quality Scorecard", h1_style))
            score_data = [
                [Paragraph("Quality Area", bold_body), Paragraph("Score", bold_body)],
                ["Overall Score", scores["overall"]],
                ["Risk Score", scores["risk"]],
                ["Robustness Score", scores["robustness"]],
                ["Optimization Score", scores["optimization"]],
                ["Walk Forward Score", scores["walk_forward"]],
                ["Monte Carlo Score", scores["monte_carlo"]],
            ]
            t_score = Table(score_data, colWidths=[200, 340])
            t_score.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (1, 0), colors.HexColor("#EDF2F7")),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                        ("PADDING", (0, 0), (-1, -1), 3),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ]
                )
            )
            story.append(t_score)
            story.append(Spacer(1, 8))

            # Module statuses
            story.append(Paragraph("Validation Modules Status", h1_style))
            status_data = [
                [Paragraph("Module", bold_body), Paragraph("Status", bold_body), Paragraph("Diagnostic / Result summary", bold_body)],
                ["Walk Forward", results.get("walk_forward", {}).get("status", "NOT RUN"), results.get("walk_forward", {}).get("message", "-")],
                ["Parameter Optimization", results.get("optimization", {}).get("status", "NOT RUN"), results.get("optimization", {}).get("message", "-")],
                ["Monte Carlo", results.get("monte_carlo", {}).get("status", "NOT RUN"), results.get("monte_carlo", {}).get("message", "-")],
                ["Robustness Stress Testing", results.get("robustness", {}).get("status", "NOT RUN"), results.get("robustness", {}).get("message", "-")],
                ["Parameter Stability", results.get("stability", {}).get("status", "NOT RUN"), results.get("stability", {}).get("message", "-")],
            ]
            t_status = Table(status_data, colWidths=[130, 80, 330])
            t_status.setStyle(
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
            story.append(t_status)

        # Trade Log
        if trade_rows:
            if not single_backtest_mode:
                story.append(PageBreak())
            story.append(Paragraph("Trade Log", h1_style))
            if total_trade_count > max_trade_log_rows:
                note = (
                    f"Showing the last {len(trade_rows)} of {total_trade_count} trades. "
                    "See trades.csv in the artifacts directory for the full list."
                )
                story.append(Paragraph(note, body_style))
                story.append(Spacer(1, 4))

            trade_data = [
                [Paragraph(h, bold_body) for h in ["Entry Time", "Dir", "Entry Price", "Exit Price", "Result", "P&L"]]
            ]
            for row in trade_rows:
                entry_time = _format_entry_time(row.get("entry_time", ""))
                trade_data.append(
                    [
                        entry_time,
                        row.get("direction", "-"),
                        row.get("entry_price", "-"),
                        row.get("exit_price", "-"),
                        row.get("result", "-"),
                        f"${row.get('pnl', '0.00')}",
                    ]
                )
            t_trades = Table(trade_data, colWidths=[110, 50, 90, 90, 60, 80], repeatRows=1)
            t_trades.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
                        ("PADDING", (0, 0), (-1, -1), 3),
                        ("FONTSIZE", (0, 0), (-1, -1), 7),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ]
                )
            )
            story.append(t_trades)

        # Plot charts if available
        heatmap_path = artifacts_dir / "stability_heatmap.png"
        equity_path = artifacts_dir / "equity_curve.png"

        if heatmap_path.exists() or equity_path.exists():
            story.append(PageBreak())
            story.append(Paragraph("Validation Visualizations", h1_style))
            if heatmap_path.exists():
                story.append(Paragraph("Parameter Stability Plateau Heatmap", bold_body))
                story.append(Image(str(heatmap_path), width=350, height=290))
                story.append(Spacer(1, 10))
            if equity_path.exists():
                story.append(Paragraph("Baseline Equity Curve", bold_body))
                story.append(Image(str(equity_path), width=350, height=180))

        doc.build(story)
        logger.info("Saved research PDF dashboard to %s", pdf_path)
