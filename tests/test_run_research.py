"""Tests for run_research.py::check_and_get_data."""

from datetime import datetime
from pathlib import Path

import pytest

import run_research
from core.exceptions import InvalidNumericDataError


def _write_csv(csv_path: Path, rows: list[str]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text("\n".join(["time,open,high,low,close,volume,spread", *rows]) + "\n")


def test_check_and_get_data_reads_and_filters_existing_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Baseline: an existing, valid data/history/{symbol}_{timeframe}_{year}.csv is read
    directly (no MT5 download attempted) and filtered to the requested date window.
    """
    monkeypatch.setattr(run_research, "PROJECT_ROOT", tmp_path)
    csv_path = tmp_path / "data" / "history" / "EURUSD_M15_2026.csv"
    _write_csv(
        csv_path,
        [
            "2026-01-06 10:00:00,1.10,1.15,1.05,1.12,100,2",
            "2026-01-06 10:15:00,1.12,1.16,1.10,1.14,110,2",
            "2026-02-01 10:00:00,1.20,1.25,1.15,1.22,120,2",  # outside filter window below
        ],
    )

    config = {
        "symbol": "EURUSD",
        "timeframe": "M15",
        "start_date": datetime(2026, 1, 1),
        "end_date": datetime(2026, 1, 31),
    }

    bars = run_research.check_and_get_data(config)

    assert len(bars) == 2


def test_check_and_get_data_rejects_invalid_ohlc_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for Bug #53: check_and_get_data must now call
    CSVDataProvider.validate(), matching run_backtest.py/run_strategy_backtest.py.
    Previously it loaded bars and returned them with no data-quality gate, so a
    corrupt CSV (e.g. high < low) would silently flow into every research module.
    """
    monkeypatch.setattr(run_research, "PROJECT_ROOT", tmp_path)
    csv_path = tmp_path / "data" / "history" / "EURUSD_M15_2026.csv"
    # high (1.0990) < low (1.1010) -- physically inconsistent OHLC bar.
    _write_csv(csv_path, ["2026-01-06 10:00:00,1.1000,1.0990,1.1010,1.1005,100,1"])

    config = {
        "symbol": "EURUSD",
        "timeframe": "M15",
        "start_date": datetime(2026, 1, 1),
        "end_date": datetime(2026, 1, 31),
    }

    with pytest.raises(InvalidNumericDataError):
        run_research.check_and_get_data(config)
