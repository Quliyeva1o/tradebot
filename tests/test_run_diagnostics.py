"""Tests for run_diagnostics.py's single-pass trend + strategy diagnostics."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from core.exceptions import InvalidNumericDataError
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


def test_run_diagnostics_for_symbol_rejects_invalid_ohlc_data(tmp_path: Path) -> None:
    """Regression test for Bug #53: run_diagnostics_for_symbol must now call
    CSVDataProvider.validate(), matching run_backtest.py/run_strategy_backtest.py.
    Previously it loaded bars and fed them straight into MarketStateBuilder/
    StrategyEngine with no data-quality gate, so a corrupt CSV (e.g. high < low)
    would silently flow into the diagnostics pass instead of failing fast.
    """
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    csv_path = history_dir / "EURUSD_M15.csv"

    t0 = datetime(2026, 1, 5, 0, 0, tzinfo=UTC)  # Monday
    rows = ["time,open,high,low,close,volume,spread"]
    # high (1.0990) < low (1.1010) -- physically inconsistent OHLC bar.
    rows.append(f"{t0.strftime('%Y-%m-%d %H:%M:%S')},1.1000,1.0990,1.1010,1.1005,100,1")
    csv_path.write_text("\n".join(rows) + "\n")

    with pytest.raises(InvalidNumericDataError):
        run_diagnostics_for_symbol("EURUSD", "M15", history_dir)
