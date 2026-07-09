"""Regression test: run_backtest.py must resolve data paths relative to the
repo root, not a hardcoded absolute path from a different machine/directory.
"""

from datetime import datetime
from pathlib import Path

import pytest

from run_backtest import check_and_get_data


def test_check_and_get_data_reads_repo_relative_history_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies check_and_get_data finds data/history/{symbol}_{timeframe}.csv
    relative to the current working directory, matching the filename format
    written by data/download_history.py (no absolute path, no year suffix).
    """
    monkeypatch.chdir(tmp_path)
    history_dir = tmp_path / "data" / "history"
    history_dir.mkdir(parents=True)
    csv_path = history_dir / "EURUSD_M15.csv"
    csv_path.write_text(
        "time,open,high,low,close,volume,spread\n"
        "2026-01-06 10:00:00,1.10,1.15,1.05,1.12,100,2\n"
        "2026-01-06 10:15:00,1.12,1.16,1.10,1.14,110,2\n"
    )

    config = {
        "symbol": "EURUSD",
        "timeframe": "M15",
        "start_date": datetime(2026, 1, 1),
        "end_date": datetime(2026, 1, 31),
    }

    bars = check_and_get_data(config)

    assert len(bars) == 2
