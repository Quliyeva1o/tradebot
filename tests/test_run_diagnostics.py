"""Tests for run_diagnostics.py's single-pass trend + strategy diagnostics."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from run_diagnostics import run_diagnostics_for_symbol


def test_run_diagnostics_for_symbol_single_pass_produces_trend_and_strategy_output(
    tmp_path: Path,
) -> None:
    """Verifies the single-pass runner reads a CSV, tallies trend distribution,
    and returns strategy diagnostics -- without crashing and with counts that
    sum to the total bar count.
    """
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    csv_path = history_dir / "EURUSD_M15.csv"

    t0 = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)  # Monday
    rows = ["time,open,high,low,close,volume,spread"]
    price = 1.1000
    for i in range(60):
        ts = t0 + timedelta(minutes=15 * i)
        wave = 0.0010 * ((i % 10) - 5)
        o = price + wave
        h = o + 0.0008
        low = o - 0.0008
        c = o + 0.0002
        rows.append(f"{ts.strftime('%Y-%m-%d %H:%M:%S')},{o:.5f},{h:.5f},{low:.5f},{c:.5f},100,1")
    csv_path.write_text("\n".join(rows) + "\n")

    diag = run_diagnostics_for_symbol("EURUSD", "M15", history_dir)

    assert diag.symbol == "EURUSD"
    assert diag.total_bars == 60
    assert sum(diag.trend_counts.values()) == 60
    assert sum(diag.trend_pct.values()) == 100.0
    assert "0_BullishContinuationStrategy" in diag.strategy_diagnostics
    assert "1_BearishContinuationStrategy" in diag.strategy_diagnostics
