"""Unit tests for MT5HistoryDownloader (mt5/history_downloader.py).

MT5 API calls are mocked throughout; no real terminal connection is made.

Regression coverage for Bug #55: unlike its sibling data/download_history.py,
MT5HistoryDownloader.download() previously called mt5.copy_rates_range() a single,
unchunked time -- MT5 fails that call outright (returns None) rather than truncating
once a request would span too many bars, which meant a wide date range/fine timeframe
could silently fail here despite the exact same range succeeding via the chunked
sibling path. These tests exercise the now-shared mt5/chunking.py-based chunking.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import mt5.history_downloader as history_downloader_module
from mt5.history_downloader import MT5HistoryDownloader


class _FakeConnector:
    """Minimal MT5Connector stand-in: always connects, disconnect is a no-op."""

    def connect(self) -> bool:
        return True

    def disconnect(self) -> None:
        pass


def _fake_symbol_info() -> SimpleNamespace:
    return SimpleNamespace(point=0.00001)


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


@pytest.fixture
def downloader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MT5HistoryDownloader:
    monkeypatch.setattr(history_downloader_module, "PROJECT_ROOT", tmp_path)
    return MT5HistoryDownloader(connector=_FakeConnector())


def test_download_chunks_a_wide_date_range_and_merges_responses(
    downloader: MT5HistoryDownloader, tmp_path: Path
) -> None:
    """Verifies download() splits the request via iter_chunk_windows and concatenates
    every chunk's rows into one CSV, instead of a single unchunked copy_rates_range call.
    """
    chunk1 = _fake_rates([(1735689600, 1.10, 1.15, 1.05, 1.12, 100, 2)])
    chunk2 = _fake_rates([(1735690500, 1.12, 1.17, 1.07, 1.14, 110, 2)])
    chunk3 = _fake_rates([(1735691400, 1.14, 1.19, 1.09, 1.16, 120, 2)])

    with (
        patch("mt5.history_downloader.mt5.symbol_info", return_value=_fake_symbol_info()),
        patch("mt5.history_downloader.mt5.copy_rates_range", side_effect=[chunk1, chunk2, chunk3]) as mock_copy,
        patch(
            "mt5.history_downloader.iter_chunk_windows",
            return_value=iter(
                [
                    (datetime(2022, 1, 1, tzinfo=UTC), datetime(2023, 1, 1, tzinfo=UTC)),
                    (datetime(2023, 1, 1, tzinfo=UTC), datetime(2024, 1, 1, tzinfo=UTC)),
                    (datetime(2024, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)),
                ]
            ),
        ),
    ):
        result_path = downloader.download(
            "EURUSD", "M15", datetime(2022, 1, 1, tzinfo=UTC), datetime(2025, 1, 1, tzinfo=UTC)
        )

    assert mock_copy.call_count == 3
    assert result_path is not None
    df = pd.read_csv(result_path)
    assert len(df) == 3
    assert list(df["close"]) == [1.12, 1.14, 1.16]


def test_download_skips_empty_chunk_without_aborting(
    downloader: MT5HistoryDownloader, tmp_path: Path
) -> None:
    """A chunk predating the broker's available history (empty response) must be
    skipped, not treated as a fatal error, as long as at least one other chunk has data.
    """
    empty_chunk = None
    real_chunk = _fake_rates([(1735689600, 1.10, 1.15, 1.05, 1.12, 100, 2)])

    with (
        patch("mt5.history_downloader.mt5.symbol_info", return_value=_fake_symbol_info()),
        patch("mt5.history_downloader.mt5.copy_rates_range", side_effect=[empty_chunk, real_chunk]),
        patch(
            "mt5.history_downloader.iter_chunk_windows",
            return_value=iter(
                [
                    (datetime(2020, 1, 1, tzinfo=UTC), datetime(2021, 1, 1, tzinfo=UTC)),
                    (datetime(2021, 1, 1, tzinfo=UTC), datetime(2022, 1, 1, tzinfo=UTC)),
                ]
            ),
        ),
    ):
        result_path = downloader.download(
            "EURUSD", "M15", datetime(2020, 1, 1, tzinfo=UTC), datetime(2022, 1, 1, tzinfo=UTC)
        )

    assert result_path is not None
    df = pd.read_csv(result_path)
    assert len(df) == 1


def test_download_dedupes_chunk_boundary_overlap(
    downloader: MT5HistoryDownloader, tmp_path: Path
) -> None:
    """Reproduces the real MT5 quirk (already handled on the data/download_history.py
    side): an out-of-range chunk can return one clipped bar identical to the true first
    bar of the next chunk. download() must dedupe it, not double-write it to the CSV.
    """
    clipped_bar_epoch = 1735689600
    stale_chunk = _fake_rates([(clipped_bar_epoch, 1.10, 1.15, 1.05, 1.12, 100, 2)])
    real_chunk = _fake_rates(
        [
            (clipped_bar_epoch, 1.10, 1.15, 1.05, 1.12, 100, 2),
            (clipped_bar_epoch + 900, 1.12, 1.17, 1.07, 1.14, 110, 2),
        ]
    )

    with (
        patch("mt5.history_downloader.mt5.symbol_info", return_value=_fake_symbol_info()),
        patch("mt5.history_downloader.mt5.copy_rates_range", side_effect=[stale_chunk, real_chunk]),
        patch(
            "mt5.history_downloader.iter_chunk_windows",
            return_value=iter(
                [
                    (datetime(2020, 1, 1, tzinfo=UTC), datetime(2021, 1, 1, tzinfo=UTC)),
                    (datetime(2021, 1, 1, tzinfo=UTC), datetime(2022, 1, 1, tzinfo=UTC)),
                ]
            ),
        ),
    ):
        result_path = downloader.download(
            "EURUSD", "M15", datetime(2020, 1, 1, tzinfo=UTC), datetime(2022, 1, 1, tzinfo=UTC)
        )

    assert result_path is not None
    df = pd.read_csv(result_path)
    assert len(df) == 2  # 1 stale duplicate removed, 2 real bars remain


def test_download_returns_none_when_all_chunks_empty(downloader: MT5HistoryDownloader) -> None:
    """Matches the pre-chunking behavior: no data anywhere in the range -> None, not a crash."""
    with (
        patch("mt5.history_downloader.mt5.symbol_info", return_value=_fake_symbol_info()),
        patch("mt5.history_downloader.mt5.copy_rates_range", return_value=None),
    ):
        result = downloader.download(
            "EURUSD", "M15", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
        )

    assert result is None


def test_download_returns_none_for_unsupported_timeframe(downloader: MT5HistoryDownloader) -> None:
    result = downloader.download(
        "EURUSD", "W1", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
    )
    assert result is None


def test_download_returns_none_when_symbol_unavailable(downloader: MT5HistoryDownloader) -> None:
    with (
        patch("mt5.history_downloader.mt5.symbol_info", return_value=None),
        patch("mt5.history_downloader.mt5.symbol_select", return_value=False),
    ):
        result = downloader.download(
            "UNKNOWN", "M15", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
        )

    assert result is None


def test_download_returns_none_when_connector_fails() -> None:
    class _FailingConnector:
        def connect(self) -> bool:
            return False

        def disconnect(self) -> None:
            pass

    downloader = MT5HistoryDownloader(connector=_FailingConnector())
    result = downloader.download(
        "EURUSD", "M15", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)
    )
    assert result is None
