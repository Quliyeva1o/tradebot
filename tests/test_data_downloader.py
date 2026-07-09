"""Unit tests for the MT5 historical data downloader and its validation pipeline.

MT5 API calls are mocked throughout; no real terminal connection is made.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from core.models import Bar
from data.download_history import (
    check_ohlc_consistency,
    detect_gaps,
    download_symbol,
    ensure_chronological_order,
    fetch_symbol_bars,
    remove_duplicate_timestamps,
    write_bars_csv,
)


def _bar(
    ts: datetime, o: float = 1.10, h: float = 1.15, low: float = 1.05, c: float = 1.12, v: float = 100.0
) -> Bar:
    return Bar(timestamp=ts, open=o, high=h, low=low, close=c, volume=v)


def _next_weekday(anchor: datetime, weekday: int) -> datetime:
    """Returns the next date on/after anchor that falls on the given weekday (Mon=0..Sun=6)."""
    return anchor + timedelta(days=(weekday - anchor.weekday()) % 7)


# --- remove_duplicate_timestamps -------------------------------------------------


def test_remove_duplicate_timestamps_drops_repeats_and_keeps_last() -> None:
    """Verifies duplicate timestamps are dropped, keeping the last-seen bar's values."""
    t0 = datetime(2026, 1, 6, 10, 0, tzinfo=UTC)
    bars = [_bar(t0, c=1.10), _bar(t0, c=1.99), _bar(t0 + timedelta(minutes=15))]

    deduped, removed = remove_duplicate_timestamps(bars)

    assert removed == 1
    assert len(deduped) == 2
    assert deduped[0].timestamp == t0
    assert deduped[0].close == 1.99  # last-seen value wins


def test_remove_duplicate_timestamps_no_duplicates() -> None:
    """Verifies a clean bar list is returned untouched."""
    t0 = datetime(2026, 1, 6, 10, 0, tzinfo=UTC)
    bars = [_bar(t0), _bar(t0 + timedelta(minutes=15))]

    deduped, removed = remove_duplicate_timestamps(bars)

    assert removed == 0
    assert deduped == bars


# --- ensure_chronological_order ---------------------------------------------------


def test_ensure_chronological_order_sorts_and_counts_violations() -> None:
    t0 = datetime(2026, 1, 6, 10, 0, tzinfo=UTC)
    bars = [_bar(t0 + timedelta(minutes=15)), _bar(t0)]

    ordered, reordered_count = ensure_chronological_order(bars)

    assert reordered_count == 1
    assert ordered[0].timestamp == t0
    assert ordered[1].timestamp == t0 + timedelta(minutes=15)


def test_ensure_chronological_order_already_sorted_is_untouched() -> None:
    t0 = datetime(2026, 1, 6, 10, 0, tzinfo=UTC)
    bars = [_bar(t0), _bar(t0 + timedelta(minutes=15))]

    ordered, reordered_count = ensure_chronological_order(bars)

    assert reordered_count == 0
    assert ordered == bars


# --- detect_gaps -------------------------------------------------------------------


def test_detect_gaps_flags_unexpected_intraweek_gap() -> None:
    tuesday = _next_weekday(datetime(2026, 1, 5, 10, 0, tzinfo=UTC), weekday=1)
    bars = [_bar(tuesday), _bar(tuesday + timedelta(hours=2))]

    gaps = detect_gaps(bars, "M15")

    assert len(gaps) == 1
    assert gaps[0].gap_duration == timedelta(hours=2)
    assert gaps[0].likely_reason != "market_closed_weekend"


def test_detect_gaps_classifies_weekend_closure_separately() -> None:
    friday = _next_weekday(datetime(2026, 1, 5, 21, 0, tzinfo=UTC), weekday=4)
    sunday_reopen = friday + timedelta(hours=49)
    bars = [_bar(friday), _bar(sunday_reopen)]

    gaps = detect_gaps(bars, "M15")

    assert len(gaps) == 1
    assert gaps[0].likely_reason == "market_closed_weekend"


def test_detect_gaps_no_gap_for_consecutive_bars() -> None:
    t0 = datetime(2026, 1, 6, 10, 0, tzinfo=UTC)
    bars = [_bar(t0), _bar(t0 + timedelta(minutes=15)), _bar(t0 + timedelta(minutes=30))]

    assert detect_gaps(bars, "M15") == []


def test_detect_gaps_never_mutates_bar_list() -> None:
    t0 = datetime(2026, 1, 6, 10, 0, tzinfo=UTC)
    bars = [_bar(t0), _bar(t0 + timedelta(hours=5))]
    original_len = len(bars)

    detect_gaps(bars, "M15")

    assert len(bars) == original_len  # gaps are logged, never filled


# --- check_ohlc_consistency ---------------------------------------------------------


def test_check_ohlc_consistency_flags_high_below_low() -> None:
    bad = _bar(datetime(2026, 1, 6, tzinfo=UTC), o=1.10, h=1.00, low=1.20, c=1.10)

    violations = check_ohlc_consistency([bad])

    assert len(violations) == 1
    assert "high" in violations[0].reason.lower()


def test_check_ohlc_consistency_flags_close_outside_range() -> None:
    bad = _bar(datetime(2026, 1, 6, tzinfo=UTC), o=1.10, h=1.15, low=1.05, c=1.20)

    violations = check_ohlc_consistency([bad])

    assert len(violations) == 1


def test_check_ohlc_consistency_flags_open_outside_range() -> None:
    bad = _bar(datetime(2026, 1, 6, tzinfo=UTC), o=1.30, h=1.15, low=1.05, c=1.10)

    violations = check_ohlc_consistency([bad])

    assert len(violations) == 1


def test_check_ohlc_consistency_no_violation_for_valid_bar() -> None:
    good = _bar(datetime(2026, 1, 6, tzinfo=UTC), o=1.10, h=1.15, low=1.05, c=1.12)

    assert check_ohlc_consistency([good]) == []


# --- fetch_symbol_bars (MT5 API mocked) ----------------------------------------------


def _fake_mt5_rates() -> np.ndarray:
    return np.array(
        [(1735689600, 1.10, 1.20, 1.00, 1.15, 100, 2, 0)],
        dtype=[
            ("time", "i8"),
            ("open", "f8"),
            ("high", "f8"),
            ("low", "f8"),
            ("close", "f8"),
            ("tick_volume", "i8"),
            ("spread", "i4"),
            ("real_volume", "i8"),
        ],
    )


def test_fetch_symbol_bars_converts_mt5_rates_to_bars() -> None:
    with (
        patch("data.download_history.mt5.symbol_select", return_value=True),
        patch("data.download_history.mt5.copy_rates_range", return_value=_fake_mt5_rates()),
    ):
        bars = fetch_symbol_bars(
            "EURUSD", "M15", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
        )

    assert len(bars) == 1
    assert bars[0].open == 1.10
    assert bars[0].volume == 100.0
    assert bars[0].spread == 2.0


def test_fetch_symbol_bars_raises_when_symbol_unavailable() -> None:
    with patch("data.download_history.mt5.symbol_select", return_value=False):
        with pytest.raises(RuntimeError):
            fetch_symbol_bars(
                "EURUSD", "M15", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
            )


def test_fetch_symbol_bars_raises_when_no_data_returned() -> None:
    with (
        patch("data.download_history.mt5.symbol_select", return_value=True),
        patch("data.download_history.mt5.copy_rates_range", return_value=None),
    ):
        with pytest.raises(RuntimeError):
            fetch_symbol_bars(
                "EURUSD", "M15", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
            )


# --- write_bars_csv ------------------------------------------------------------------


def test_write_bars_csv_writes_bar_compatible_header_and_rows(tmp_path: Path) -> None:
    bars = [
        _bar(datetime(2026, 1, 6, 10, 0, tzinfo=UTC)),
        _bar(datetime(2026, 1, 6, 10, 15, tzinfo=UTC)),
    ]

    path = write_bars_csv(bars, "EURUSD", "M15", tmp_path)

    assert path == tmp_path / "EURUSD_M15.csv"
    lines = path.read_text().strip().splitlines()
    assert lines[0] == "time,open,high,low,close,volume,spread"
    assert len(lines) == 3


# --- download_symbol (orchestration) -------------------------------------------------


def test_download_symbol_deduplicates_validates_and_writes(tmp_path: Path) -> None:
    t0 = datetime(2026, 1, 6, 10, 0, tzinfo=UTC)
    fake_bars = [_bar(t0), _bar(t0), _bar(t0 + timedelta(minutes=15))]

    with patch("data.download_history.fetch_symbol_bars", return_value=fake_bars):
        report = download_symbol(
            "EURUSD", "M15", t0, t0 + timedelta(days=1), tmp_path
        )

    assert report.duplicates_removed == 1
    assert report.total_bars == 2
    assert (tmp_path / "EURUSD_M15.csv").exists()
