"""Unit tests for MT5Connector.fetch_recent_bars() (mt5/connector.py).

MT5 API calls are mocked throughout; no real terminal connection is made.
"""

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from mt5.connector import MT5Connector


def _symbol_info(point: float) -> SimpleNamespace:
    """Minimal stand-in for MT5's SymbolInfo, exposing only what we read (.point)."""
    return SimpleNamespace(point=point)


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


class TestFetchRecentBars:
    def test_fetches_from_start_pos_1_to_skip_the_forming_bar(self) -> None:
        """Position 0 is MT5's currently-forming, not-yet-closed bar -- fetch_recent_bars
        must always request starting at position 1 so only closed bars are returned.
        """
        rates = _fake_rates([(1735689600, 1.10, 1.15, 1.05, 1.12, 100, 2)])
        connector = MT5Connector()

        with (
            patch("mt5.connector.mt5.symbol_select", return_value=True) as mock_select,
            patch("mt5.connector.mt5.symbol_info", return_value=_symbol_info(point=0.01)),
            patch("mt5.connector.mt5.copy_rates_from_pos", return_value=rates) as mock_copy,
        ):
            bars = connector.fetch_recent_bars("USTEC", "M5", count=10)

        mock_select.assert_called_once_with("USTEC", True)
        args, _kwargs = mock_copy.call_args
        assert args[2] == 1  # start_pos
        assert args[3] == 10  # count
        assert len(bars) == 1

    def test_converts_rates_to_bars_with_point_scaled_spread(self) -> None:
        """Spread comes back from MT5 as an integer point count, not a price-space
        value -- it must be multiplied by the symbol's point size (e.g. 0.01 for
        an index like USTEC), exactly like data/download_history.py already does.
        """
        rates = _fake_rates([(1735689600, 29200.0, 29250.0, 29150.0, 29220.0, 500, 100)])
        connector = MT5Connector()

        with (
            patch("mt5.connector.mt5.symbol_select", return_value=True),
            patch("mt5.connector.mt5.symbol_info", return_value=_symbol_info(point=0.01)),
            patch("mt5.connector.mt5.copy_rates_from_pos", return_value=rates),
        ):
            bars = connector.fetch_recent_bars("USTEC", "M5", count=1)

        assert len(bars) == 1
        bar = bars[0]
        assert bar.open == 29200.0
        assert bar.high == 29250.0
        assert bar.low == 29150.0
        assert bar.close == 29220.0
        assert bar.volume == 500.0
        assert bar.spread == pytest.approx(1.0)  # 100 points * 0.01 point size

    def test_returns_bars_in_chronological_order(self) -> None:
        rates = _fake_rates(
            [
                (1735689600, 1.10, 1.15, 1.05, 1.12, 100, 2),
                (1735690500, 1.12, 1.17, 1.07, 1.14, 110, 2),
                (1735691400, 1.14, 1.19, 1.09, 1.16, 120, 2),
            ]
        )
        connector = MT5Connector()

        with (
            patch("mt5.connector.mt5.symbol_select", return_value=True),
            patch("mt5.connector.mt5.symbol_info", return_value=_symbol_info(point=0.00001)),
            patch("mt5.connector.mt5.copy_rates_from_pos", return_value=rates),
        ):
            bars = connector.fetch_recent_bars("EURUSD", "M15", count=3)

        assert [b.close for b in bars] == [1.12, 1.14, 1.16]
        assert bars[0].timestamp < bars[1].timestamp < bars[2].timestamp

    def test_raises_for_unsupported_timeframe(self) -> None:
        connector = MT5Connector()
        with pytest.raises(RuntimeError, match="Unsupported timeframe"):
            connector.fetch_recent_bars("USTEC", "W1", count=10)

    def test_raises_when_symbol_unavailable(self) -> None:
        connector = MT5Connector()
        with patch("mt5.connector.mt5.symbol_select", return_value=False):
            with pytest.raises(RuntimeError, match="not available"):
                connector.fetch_recent_bars("UNKNOWN", "M5", count=10)

    def test_raises_when_no_rates_returned(self) -> None:
        connector = MT5Connector()
        with (
            patch("mt5.connector.mt5.symbol_select", return_value=True),
            patch("mt5.connector.mt5.symbol_info", return_value=_symbol_info(point=0.01)),
            patch("mt5.connector.mt5.copy_rates_from_pos", return_value=None),
        ):
            with pytest.raises(RuntimeError, match="No recent rates"):
                connector.fetch_recent_bars("USTEC", "M5", count=10)

    def test_raises_when_rates_empty(self) -> None:
        connector = MT5Connector()
        with (
            patch("mt5.connector.mt5.symbol_select", return_value=True),
            patch("mt5.connector.mt5.symbol_info", return_value=_symbol_info(point=0.01)),
            patch("mt5.connector.mt5.copy_rates_from_pos", return_value=_fake_rates([])),
        ):
            with pytest.raises(RuntimeError, match="No recent rates"):
                connector.fetch_recent_bars("USTEC", "M5", count=10)
