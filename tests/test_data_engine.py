"""Pytest test suite for Enterprise Data Engine.

Tests all required edge cases for data loading, parsing, settings policies,
and custom exceptions.
"""

from pathlib import Path

import pandas as pd
import pytest

from config.settings import Settings
from core.data_engine import DataEngine
from core.exceptions import (
    DuplicateTimestampError,
    EmptyDataError,
    InvalidNumericDataError,
    InvalidTimestampError,
    MissingColumnError,
)
from data.csv_provider import CSVDataProvider


def test_valid_csv(tmp_path: Path) -> None:
    """Verifies that a valid CSV is loaded and normalized correctly."""
    csv_file = tmp_path / "valid.csv"
    csv_file.write_text(
        "Timestamp,Open,High,Low,Close,Volume\n"
        "2026-07-01 12:00:00,1.1000,1.1050,1.0990,1.1020,1000\n"
        "2026-07-01 13:00:00,1.1020,1.1080,1.1010,1.1060,1200\n"
    )

    provider = CSVDataProvider(filepath=csv_file)
    engine = DataEngine(provider=provider)
    df = engine.get_data()

    assert len(df) == 2
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert df.loc[0, "time"] == pd.to_datetime("2026-07-01 12:00:00").tz_localize("UTC")
    assert df.loc[1, "close"] == 1.1060


def test_missing_column(tmp_path: Path) -> None:
    """Verifies that MissingColumnError is raised when required columns are absent."""
    csv_file = tmp_path / "missing_col.csv"
    # Missing 'Close' column
    csv_file.write_text(
        "Timestamp,Open,High,Low,Volume\n"
        "2026-07-01 12:00:00,1.1000,1.1050,1.0990,1000\n"
    )

    provider = CSVDataProvider(filepath=csv_file)
    engine = DataEngine(provider=provider)

    with pytest.raises(MissingColumnError) as exc_info:
        engine.get_data()

    assert "close" in exc_info.value.missing_cols


def test_invalid_datetime(tmp_path: Path) -> None:
    """Verifies that InvalidTimestampError is raised for unparseable dates."""
    csv_file = tmp_path / "invalid_date.csv"
    csv_file.write_text(
        "Timestamp,Open,High,Low,Close,Volume\n"
        "bad-date-string,1.1000,1.1050,1.0990,1.1020,1000\n"
    )

    provider = CSVDataProvider(filepath=csv_file)
    engine = DataEngine(provider=provider)

    with pytest.raises(InvalidTimestampError):
        engine.get_data()


def test_invalid_numeric_value(tmp_path: Path) -> None:
    """Verifies that InvalidNumericDataError is raised for non-numeric fields."""
    csv_file = tmp_path / "invalid_numeric.csv"
    csv_file.write_text(
        "Timestamp,Open,High,Low,Close,Volume\n"
        "2026-07-01 12:00:00,1.1000,corrupted-text,1.0990,1.1020,1000\n"
    )

    provider = CSVDataProvider(filepath=csv_file)
    engine = DataEngine(provider=provider)

    with pytest.raises(InvalidNumericDataError):
        engine.get_data()


def test_empty_dataframe(tmp_path: Path) -> None:
    """Verifies that EmptyDataError is raised when CSV file has no records."""
    csv_file = tmp_path / "empty.csv"
    csv_file.write_text("Timestamp,Open,High,Low,Close,Volume\n")

    provider = CSVDataProvider(filepath=csv_file)
    engine = DataEngine(provider=provider)

    with pytest.raises(EmptyDataError):
        engine.get_data()


def test_unsorted_timestamps(tmp_path: Path) -> None:
    """Verifies that unsorted records are sorted chronologically by the provider."""
    csv_file = tmp_path / "unsorted.csv"
    csv_file.write_text(
        "Timestamp,Open,High,Low,Close,Volume\n"
        "2026-07-01 13:00:00,1.1020,1.1080,1.1010,1.1060,1200\n"
        "2026-07-01 12:00:00,1.1000,1.1050,1.0990,1.1020,1000\n"
    )

    provider = CSVDataProvider(filepath=csv_file)
    engine = DataEngine(provider=provider)
    df = engine.get_data()

    # The provider must sort these automatically
    assert df.loc[0, "time"] == pd.to_datetime("2026-07-01 12:00:00").tz_localize("UTC")
    assert df.loc[1, "time"] == pd.to_datetime("2026-07-01 13:00:00").tz_localize("UTC")


def test_duplicate_removal_enabled(tmp_path: Path) -> None:
    """Verifies duplicate policy settings (drop vs keep)."""
    csv_file = tmp_path / "duplicates.csv"
    csv_file.write_text(
        "Timestamp,Open,High,Low,Close,Volume\n"
        "2026-07-01 12:00:00,1.1000,1.1050,1.0990,1.1020,1000\n"
        "2026-07-01 12:00:00,1.1010,1.1060,1.1000,1.1030,1100\n"
    )

    # Scenario A: DUPLICATE_POLICY = "drop" (default)
    settings_drop = Settings(DUPLICATE_POLICY="drop")
    provider_drop = CSVDataProvider(filepath=csv_file, settings=settings_drop)
    engine_drop = DataEngine(provider=provider_drop)
    df_drop = engine_drop.get_data()

    # Verify duplicate was dropped (keeps the last one)
    assert len(df_drop) == 1
    assert df_drop.loc[0, "close"] == 1.1030

    # Scenario B: DUPLICATE_POLICY = "keep"
    # Keeping duplicates will cause the engine validation to fail with DuplicateTimestampError
    settings_keep = Settings(DUPLICATE_POLICY="keep")
    provider_keep = CSVDataProvider(filepath=csv_file, settings=settings_keep)
    engine_keep = DataEngine(provider=provider_keep)

    with pytest.raises(DuplicateTimestampError):
        engine_keep.get_data()
