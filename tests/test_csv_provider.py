"""Unit tests for CSVDataProvider's MISSING_VALUE_POLICY handling and
provider.validate() (Bug #28-new: policy="raise" previously did nothing --
NaN values silently flowed into Bar objects unless a separate DataEngine
wrapper happened to be used downstream)."""

from datetime import datetime
from pathlib import Path

import pytest

from config.settings import Settings
from core.exceptions import InvalidNumericDataError
from data.csv_provider import CSVDataProvider


def _write_csv_with_nan(tmp_path: Path) -> Path:
    csv_file = tmp_path / "nan_data.csv"
    csv_file.write_text(
        "time,open,high,low,close,volume\n"
        "2026-01-01 00:00:00,1.1000,1.1050,1.0950,,1000\n"
        "2026-01-01 00:15:00,1.1010,1.1060,1.0960,1.1030,1100\n"
        "2026-01-01 00:30:00,1.1020,1.1070,1.0970,1.1040,1200\n"
    )
    return csv_file


class TestMissingValuePolicy:
    """load() must actually enforce MISSING_VALUE_POLICY, not just for
    "drop"/"fill" -- "raise" must raise directly, not rely on a separate
    downstream validator that may never run (e.g. run_backtest.py calls
    CSVDataProvider.load() directly, without DataEngine)."""

    def test_raise_policy_raises_on_nan(self, tmp_path: Path) -> None:
        csv_file = _write_csv_with_nan(tmp_path)
        settings = Settings(MISSING_VALUE_POLICY="raise")
        provider = CSVDataProvider(filepath=csv_file, settings=settings)

        with pytest.raises(InvalidNumericDataError, match="close"):
            provider.load()

    def test_drop_policy_removes_nan_rows(self, tmp_path: Path) -> None:
        csv_file = _write_csv_with_nan(tmp_path)
        settings = Settings(MISSING_VALUE_POLICY="drop")
        provider = CSVDataProvider(filepath=csv_file, settings=settings)

        bars = provider.load()

        assert len(bars) == 2
        assert all(b.close == b.close for b in bars)  # no NaNs (nan != nan)

    def test_fill_policy_fills_nan_via_ffill_bfill(self, tmp_path: Path) -> None:
        csv_file = _write_csv_with_nan(tmp_path)
        settings = Settings(MISSING_VALUE_POLICY="fill")
        provider = CSVDataProvider(filepath=csv_file, settings=settings)

        bars = provider.load()

        assert len(bars) == 3
        # First row's close was NaN; ffill has nothing before it, so bfill
        # takes over and pulls the next row's close (1.1030).
        assert bars[0].close == 1.1030

    def test_no_nan_present_is_unaffected_by_any_policy(self, tmp_path: Path) -> None:
        csv_file = tmp_path / "clean.csv"
        csv_file.write_text(
            "time,open,high,low,close,volume\n"
            "2026-01-01 00:00:00,1.1000,1.1050,1.0950,1.1020,1000\n"
        )
        for policy in ("raise", "drop", "fill"):
            settings = Settings(MISSING_VALUE_POLICY=policy)
            provider = CSVDataProvider(filepath=csv_file, settings=settings)
            bars = provider.load()
            assert len(bars) == 1


class TestValidate:
    """provider.validate() catches non-positive prices -- now actually
    invoked by run_backtest.py after load(), previously never called there."""

    def test_validate_raises_on_zero_price(self) -> None:
        from core.models import Bar

        provider = CSVDataProvider(filepath="unused.csv")
        bad_bar = Bar(
            timestamp=datetime(2026, 1, 1),
            open=1.10,
            high=1.11,
            low=0.0,
            close=1.105,
            volume=100.0,
        )

        with pytest.raises(InvalidNumericDataError, match="low"):
            provider.validate([bad_bar])

    def test_validate_passes_for_positive_prices(self) -> None:
        from core.models import Bar

        provider = CSVDataProvider(filepath="unused.csv")
        good_bar = Bar(
            timestamp=datetime(2026, 1, 1),
            open=1.10,
            high=1.11,
            low=1.09,
            close=1.105,
            volume=100.0,
        )

        provider.validate([good_bar])  # must not raise


class TestValidateOHLCConsistency:
    """provider.validate() catches internally-inconsistent OHLC bars
    (Bug #31 -- previously only checked non-positive prices, missing a
    whole class of physically-impossible bars: high < low, or a close/open
    outside the [low, high] range)."""

    def test_validate_raises_on_high_below_low(self) -> None:
        from core.models import Bar

        provider = CSVDataProvider(filepath="unused.csv")
        bad_bar = Bar(
            timestamp=datetime(2026, 1, 1),
            open=1.10,
            high=1.05,
            low=1.15,
            close=1.10,
            volume=100.0,
        )

        with pytest.raises(InvalidNumericDataError, match="OHLC consistency"):
            provider.validate([bad_bar])

    def test_validate_raises_on_close_outside_range(self) -> None:
        from core.models import Bar

        provider = CSVDataProvider(filepath="unused.csv")
        bad_bar = Bar(
            timestamp=datetime(2026, 1, 1),
            open=1.10,
            high=1.12,
            low=1.08,
            close=1.20,  # above high
            volume=100.0,
        )

        with pytest.raises(InvalidNumericDataError, match="OHLC consistency"):
            provider.validate([bad_bar])

    def test_validate_passes_for_internally_consistent_bar(self) -> None:
        from core.models import Bar

        provider = CSVDataProvider(filepath="unused.csv")
        good_bar = Bar(
            timestamp=datetime(2026, 1, 1),
            open=1.10,
            high=1.12,
            low=1.08,
            close=1.105,
            volume=100.0,
        )

        provider.validate([good_bar])  # must not raise
