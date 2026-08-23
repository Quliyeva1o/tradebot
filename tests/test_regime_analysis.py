"""Unit tests for research/regime_analysis.py."""

from datetime import datetime, timedelta

import pytest

from core.models import Bar, Timeframe
from research.regime_analysis import (
    RegimeType,
    analyze_regime,
    classify_regime,
    compute_autocorrelation,
    compute_move_statistics,
    compute_volatility_regime,
)

_START = datetime(2026, 1, 1)


def _bars_from_closes(closes: list[float]) -> list[Bar]:
    bars = []
    for i, close in enumerate(closes):
        bars.append(
            Bar(
                timestamp=_START + timedelta(minutes=5 * i),
                open=close,
                high=close + 0.5,
                low=close - 0.5,
                close=close,
                volume=100.0,
            )
        )
    return bars


def _bars_from_ranges(ranges: list[float]) -> list[Bar]:
    """Builds bars with a given high-low range each, flat close-to-close."""
    bars = []
    price = 100.0
    for i, rng in enumerate(ranges):
        bars.append(
            Bar(
                timestamp=_START + timedelta(minutes=5 * i),
                open=price,
                high=price + rng / 2,
                low=price - rng / 2,
                close=price,
                volume=100.0,
            )
        )
    return bars


class TestComputeAutocorrelation:
    def test_persistent_runs_produce_positive_autocorrelation(self) -> None:
        # Returns alternate in blocks (1,1,1,1, 5,5,5,5, ...): consecutive
        # returns are usually equal -> strong positive lag-1 autocorrelation.
        closes = [0.0]
        block_returns = ([1.0] * 4 + [5.0] * 4) * 4
        for r in block_returns:
            closes.append(closes[-1] + r)

        autocorr = compute_autocorrelation(_bars_from_closes(closes))

        assert autocorr > 0.5

    def test_alternating_returns_produce_negative_autocorrelation(self) -> None:
        # Returns strictly alternate +5/-5: perfectly anti-correlated at lag 1.
        closes = [0.0]
        for i in range(20):
            closes.append(closes[-1] + (5.0 if i % 2 == 0 else -5.0))

        autocorr = compute_autocorrelation(_bars_from_closes(closes))

        assert autocorr == pytest.approx(-1.0, abs=1e-9)

    def test_insufficient_data_returns_zero(self) -> None:
        bars = _bars_from_closes([100.0, 101.0])
        assert compute_autocorrelation(bars, lag=1) == 0.0

    def test_empty_bars_returns_zero(self) -> None:
        assert compute_autocorrelation([]) == 0.0


class TestClassifyRegime:
    @pytest.mark.parametrize(
        ("autocorr", "expected"),
        [
            (0.5, RegimeType.TRENDING),
            (0.1, RegimeType.TRENDING),
            (0.0, RegimeType.RANGING),
            (-0.05, RegimeType.RANGING),
            (-0.1, RegimeType.MEAN_REVERTING),
            (-0.5, RegimeType.MEAN_REVERTING),
        ],
    )
    def test_threshold_boundaries(self, autocorr: float, expected: RegimeType) -> None:
        assert classify_regime(autocorr) == expected


class TestComputeVolatilityRegime:
    def test_expanding_volatility_buckets_as_high(self) -> None:
        ranges = [1.0] * 30 + [10.0] * 20
        bars = _bars_from_ranges(ranges)

        regime = compute_volatility_regime(bars, lookback=50, atr_period=5)

        assert regime.bucket == "high"
        assert regime.atr_percentile > 67.0

    def test_mid_range_volatility_buckets_as_normal(self) -> None:
        # Low, then mid, then a high spike, then decaying back toward mid --
        # Wilder's smoothing leaves the final ATR reading roughly mid-pack
        # relative to its own trailing history (neither a new low nor high).
        ranges = [1.0] * 15 + [2.0] * 15 + [5.0] * 15 + [2.0] * 5
        bars = _bars_from_ranges(ranges)

        regime = compute_volatility_regime(bars, lookback=50, atr_period=5)

        assert regime.bucket == "normal"
        assert 33.0 <= regime.atr_percentile <= 67.0

    def test_empty_bars_returns_zero_atr(self) -> None:
        regime = compute_volatility_regime([])
        assert regime.atr == 0.0
        assert regime.bucket == "normal"


class TestComputeMoveStatistics:
    def test_known_values(self) -> None:
        stats = compute_move_statistics(_bars_from_closes([100.0, 101.0, 99.0, 102.0]))

        assert stats.mean_move == pytest.approx(0.6667, abs=1e-3)
        assert stats.median_move == pytest.approx(1.0)
        assert stats.stdev_move == pytest.approx(2.5166, abs=1e-3)
        assert stats.up_bar_pct == pytest.approx(66.6667, abs=1e-3)
        assert stats.down_bar_pct == pytest.approx(33.3333, abs=1e-3)

    def test_fewer_than_two_bars_returns_all_zero(self) -> None:
        stats = compute_move_statistics(_bars_from_closes([100.0]))
        assert stats == compute_move_statistics([])


class TestAnalyzeRegime:
    def test_orchestrates_and_trims_to_window(self) -> None:
        closes = [100.0 + i for i in range(300)]
        bars = _bars_from_closes(closes)

        summary = analyze_regime(bars, symbol="EURUSD", timeframe=Timeframe.M5, window_bars=50)

        assert summary.symbol == "EURUSD"
        assert summary.timeframe == Timeframe.M5
        assert summary.window_bars == 50
        assert isinstance(summary.regime, RegimeType)

    def test_non_positive_window_bars_uses_full_sequence(self) -> None:
        bars = _bars_from_closes([100.0 + i for i in range(10)])
        summary = analyze_regime(bars, symbol="EURUSD", timeframe=Timeframe.M5, window_bars=0)
        assert summary.window_bars == 10
