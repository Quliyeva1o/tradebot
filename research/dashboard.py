"""Research Dashboard and PDF Reporting module."""

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

logger = setup_logger("dashboard")


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
    ) -> None:
        """Compiles the aggregated results into Markdown and ReportLab PDF dashboard formats.

        Args:
            symbol: Trading instrument.
            timeframe: Timeframe of simulation.
            results: Dictionary containing outcomes and status logs from each modular step.
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

        # 1. Export research_dashboard.md
        self._export_markdown(symbol, timeframe, results, scores)

        # 2. Export research_summary.pdf
        self._export_pdf(symbol, timeframe, results, scores)

    def _export_markdown(self, symbol: str, timeframe: str, results: dict[str, Any], scores: dict[str, Any]) -> None:
        """Exports MD research dashboard."""
        artifacts_dir = Path("c:/Users/Microsol/Desktop/trade/artifacts")
        md_path = artifacts_dir / "research_dashboard.md"

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
        with open(md_path, "w") as f:
            f.write(md)
        logger.info("Saved research MD dashboard to %s", md_path)

    def _export_pdf(self, symbol: str, timeframe: str, results: dict[str, Any], scores: dict[str, Any]) -> None:
        """Exports PDF summary using ReportLab."""
        artifacts_dir = Path("c:/Users/Microsol/Desktop/trade/artifacts")
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
