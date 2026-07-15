"""Unit tests for the ResearchDashboard Trade Log section."""

import csv
from pathlib import Path
from typing import Any

import pytest

from research.dashboard import ResearchDashboard


@pytest.fixture(autouse=True)
def _isolated_artifacts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirects dashboard output to a temp directory so tests never touch the real artifacts/ dir."""
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    monkeypatch.setenv("TRADEBOT_ARTIFACTS_DIR", str(artifacts_dir))
    return artifacts_dir


def _write_trades_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Writes a minimal trades.csv with the subset of columns the Trade Log needs."""
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["entry_time", "exit_time", "direction", "entry_price", "exit_price", "result", "pnl"])
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _make_trade_row(entry_time: str, direction: str = "BUY", result: str = "WIN", pnl: str = "100.00") -> dict[str, str]:
    return {
        "entry_time": entry_time,
        "exit_time": entry_time,
        "direction": direction,
        "entry_price": "1.10000",
        "exit_price": "1.10100",
        "result": result,
        "pnl": pnl,
    }


def test_dashboard_without_trades_csv_omits_trade_log(_isolated_artifacts_dir: Path) -> None:
    """Regression guard: omitting trades_csv_path must not change existing dashboard output."""
    ResearchDashboard().run("EURUSD", "H1", results={})

    md_content = (_isolated_artifacts_dir / "research_dashboard.md").read_text()
    assert "Trade Log" not in md_content
    assert (_isolated_artifacts_dir / "research_summary.pdf").exists()


def test_dashboard_with_missing_trades_csv_omits_trade_log(_isolated_artifacts_dir: Path) -> None:
    """A trades_csv_path that doesn't exist should be skipped, not raise."""
    ResearchDashboard().run(
        "EURUSD", "H1", results={}, trades_csv_path=_isolated_artifacts_dir / "does_not_exist.csv"
    )

    md_content = (_isolated_artifacts_dir / "research_dashboard.md").read_text()
    assert "Trade Log" not in md_content


def test_dashboard_renders_trade_log_from_csv(_isolated_artifacts_dir: Path) -> None:
    """All configured trades should render as rows when under the max_trade_log_rows cap."""
    trades_csv = _isolated_artifacts_dir / "trades.csv"
    _write_trades_csv(
        trades_csv,
        [
            _make_trade_row("2026-01-01 05:00:00", direction="BUY", result="WIN", pnl="150.25"),
            _make_trade_row("2026-01-02 09:30:00", direction="SELL", result="LOSS", pnl="-42.50"),
        ],
    )

    ResearchDashboard().run("EURUSD", "H1", results={}, trades_csv_path=trades_csv)

    md_content = (_isolated_artifacts_dir / "research_dashboard.md").read_text()
    assert "## Trade Log" in md_content
    assert "2026-01-01 05:00" in md_content
    assert "2026-01-02 09:30" in md_content
    assert "BUY" in md_content and "SELL" in md_content
    assert "WIN" in md_content and "LOSS" in md_content
    assert "$150.25" in md_content
    assert "$-42.50" in md_content
    assert "Showing the last" not in md_content


def test_dashboard_truncates_trade_log_to_max_rows(_isolated_artifacts_dir: Path) -> None:
    """When trade count exceeds max_trade_log_rows, only the most recent N should render."""
    trades_csv = _isolated_artifacts_dir / "trades.csv"
    rows = [_make_trade_row(f"2026-01-0{i}T00:00:00", result="WIN" if i % 2 else "LOSS") for i in range(1, 6)]
    _write_trades_csv(trades_csv, rows)

    ResearchDashboard().run("EURUSD", "H1", results={}, trades_csv_path=trades_csv, max_trade_log_rows=2)

    md_content = (_isolated_artifacts_dir / "research_dashboard.md").read_text()
    assert "Showing the last 2 of 5 trades" in md_content
    # Only the last 2 (2026-01-04, 2026-01-05) entry times should appear as trade rows.
    assert "2026-01-04 00:00" in md_content
    assert "2026-01-05 00:00" in md_content
    assert "2026-01-01 00:00" not in md_content


def test_dashboard_pdf_generation_does_not_raise_with_trade_log(_isolated_artifacts_dir: Path) -> None:
    """The PDF export path must also handle the Trade Log section without error."""
    trades_csv = _isolated_artifacts_dir / "trades.csv"
    _write_trades_csv(trades_csv, [_make_trade_row("2026-01-01 05:00:00")])

    ResearchDashboard().run("EURUSD", "H1", results={}, trades_csv_path=trades_csv)

    pdf_path = _isolated_artifacts_dir / "research_summary.pdf"
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 0


def test_optimizer_best_pnl_flows_into_dashboard_optimization_score(_isolated_artifacts_dir: Path) -> None:
    """Regression test for Bug #20: a populated best_pnl must produce a numeric Optimization Score."""
    results: dict[str, Any] = {
        "robustness": {
            "status": "SUCCESS",
            "data": {"baseline": {"net_profit": 100.0, "max_drawdown": 0.1}},
        },
        "optimization": {
            "status": "SUCCESS",
            "data": {"best_params": {"stop_buffer_pips": 5.0}, "best_pnl": 500.0},
            "message": "Optimal Parameters: StopBuffer=5.0",
        },
    }

    ResearchDashboard().run("EURUSD", "H1", results=results)

    md_content = (_isolated_artifacts_dir / "research_dashboard.md").read_text()
    assert "Optimization Stability Score**: N/A" not in md_content
