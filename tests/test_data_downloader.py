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
    _iter_chunk_windows,
    check_ohlc_consistency,
    detect_gaps,
    download_symbol,
    ensure_chronological_order,
    fetch_symbol_bars,
    fetch_symbol_bars_chunked,
    remove_duplicate_timestamps,
    validate_bars,
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


# --- _iter_chunk_windows / fetch_symbol_bars_chunked (multi-chunk merge) -------------


def _fake_rates(rows: list[tuple[int, float, float, float, float, int, int]]) -> np.ndarray:
    """Builds a fake MT5 rates array from (epoch, o, h, l, c, tick_volume, spread) rows."""
    return np.array(
        [(*row, 0) for row in rows],
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


def test_iter_chunk_windows_covers_full_range_without_gaps() -> None:
    start = datetime(2022, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 1, tzinfo=UTC)

    windows = list(_iter_chunk_windows(start, end, "M15"))

    assert len(windows) > 1  # a 4-year M15 range must be split into multiple chunks
    assert windows[0][0] == start
    assert windows[-1][1] == end
    # Windows must be contiguous: each chunk's end is the next chunk's start.
    for (_, prev_end), (next_start, _) in zip(windows, windows[1:], strict=False):
        assert prev_end == next_start


def test_iter_chunk_windows_single_chunk_for_small_range() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 1, 2, tzinfo=UTC)

    windows = list(_iter_chunk_windows(start, end, "M15"))

    assert windows == [(start, end)]


def test_fetch_symbol_bars_chunked_merges_multiple_chunk_responses() -> None:
    """Verifies bars from several successive MT5 responses are concatenated in order."""
    chunk1 = _fake_rates([(1735689600, 1.10, 1.15, 1.05, 1.12, 100, 2)])
    chunk2 = _fake_rates([(1735690500, 1.12, 1.17, 1.07, 1.14, 110, 2)])
    chunk3 = _fake_rates([(1735691400, 1.14, 1.19, 1.09, 1.16, 120, 2)])

    with (
        patch("data.download_history.mt5.symbol_select", return_value=True),
        patch(
            "data.download_history.mt5.copy_rates_range",
            side_effect=[chunk1, chunk2, chunk3],
        ),
        patch(
            "data.download_history._iter_chunk_windows",
            return_value=iter(
                [
                    (datetime(2022, 1, 1, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC)),
                    (datetime(2023, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)),
                    (datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
                ]
            ),
        ),
    ):
        bars = fetch_symbol_bars_chunked(
            "EURUSD", "M15", datetime(2022, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)
        )

    assert len(bars) == 3
    assert [b.close for b in bars] == [1.12, 1.14, 1.16]


def test_fetch_symbol_bars_chunked_skips_empty_chunk_without_aborting() -> None:
    """Simulates a chunk predating the broker's history (empty response) mixed with real data."""
    empty_chunk = None  # MT5 returns None, not [], for a truly out-of-range window
    real_chunk = _fake_rates([(1735689600, 1.10, 1.15, 1.05, 1.12, 100, 2)])

    with (
        patch("data.download_history.mt5.symbol_select", return_value=True),
        patch(
            "data.download_history.mt5.copy_rates_range",
            side_effect=[empty_chunk, real_chunk],
        ),
        patch(
            "data.download_history._iter_chunk_windows",
            return_value=iter(
                [
                    (datetime(2020, 1, 1, tzinfo=UTC), datetime(2021, 1, 1, tzinfo=UTC)),
                    (datetime(2021, 1, 1, tzinfo=UTC), datetime(2022, 1, 1, tzinfo=UTC)),
                ]
            ),
        ),
    ):
        bars = fetch_symbol_bars_chunked(
            "EURUSD", "M15", datetime(2020, 1, 1, tzinfo=UTC), datetime(2022, 1, 1, tzinfo=UTC)
        )

    assert len(bars) == 1


def test_fetch_symbol_bars_chunked_raises_when_all_chunks_empty() -> None:
    with (
        patch("data.download_history.mt5.symbol_select", return_value=True),
        patch("data.download_history.mt5.copy_rates_range", return_value=None),
    ):
        with pytest.raises(RuntimeError):
            fetch_symbol_bars_chunked(
                "EURUSD", "M15", datetime(2020, 1, 1, tzinfo=UTC), datetime(2020, 1, 2, tzinfo=UTC)
            )


def test_fetch_symbol_bars_chunked_raises_when_symbol_unavailable() -> None:
    with patch("data.download_history.mt5.symbol_select", return_value=False):
        with pytest.raises(RuntimeError):
            fetch_symbol_bars_chunked(
                "EURUSD", "M15", datetime(2020, 1, 1, tzinfo=UTC), datetime(2020, 1, 2, tzinfo=UTC)
            )


def test_fetch_symbol_bars_chunked_then_validate_dedupes_chunk_boundary_overlap() -> None:
    """Reproduces the real MT5 quirk: an out-of-range chunk returns one clipped bar
    identical to the true first bar of the next chunk. validate_bars must dedupe it
    away rather than treat it as fabricated/duplicated data or a false gap.
    """
    clipped_bar_epoch = 1735689600  # the "real" earliest bar MT5 clips back to
    stale_chunk = _fake_rates([(clipped_bar_epoch, 1.10, 1.15, 1.05, 1.12, 100, 2)])
    real_chunk = _fake_rates(
        [
            (clipped_bar_epoch, 1.10, 1.15, 1.05, 1.12, 100, 2),
            (clipped_bar_epoch + 900, 1.12, 1.17, 1.07, 1.14, 110, 2),
        ]
    )

    with (
        patch("data.download_history.mt5.symbol_select", return_value=True),
        patch(
            "data.download_history.mt5.copy_rates_range",
            side_effect=[stale_chunk, real_chunk],
        ),
        patch(
            "data.download_history._iter_chunk_windows",
            return_value=iter(
                [
                    (datetime(2020, 1, 1, tzinfo=UTC), datetime(2021, 1, 1, tzinfo=UTC)),
                    (datetime(2021, 1, 1, tzinfo=UTC), datetime(2022, 1, 1, tzinfo=UTC)),
                ]
            ),
        ),
    ):
        raw_bars = fetch_symbol_bars_chunked(
            "EURUSD", "M15", datetime(2020, 1, 1, tzinfo=UTC), datetime(2022, 1, 1, tzinfo=UTC)
        )

    assert len(raw_bars) == 3  # 1 stale + 2 real, before validation

    validated, report = validate_bars(raw_bars, "EURUSD", "M15")

    assert len(validated) == 2
    assert report.duplicates_removed == 1


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

    with patch("data.download_history.fetch_symbol_bars_chunked", return_value=fake_bars):
        report = download_symbol(
            "EURUSD", "M15", t0, t0 + timedelta(days=1), tmp_path
        )

    assert report.duplicates_removed == 1
    assert report.total_bars == 2
    assert (tmp_path / "EURUSD_M15.csv").exists()
