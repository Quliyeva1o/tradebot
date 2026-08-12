"""Statistical market-regime analysis: trending vs mean-reverting vs ranging.

Also computes volatility regime and move statistics. Decision-support only
-- not wired into StrategyEngine. Operates directly on list[Bar]/Sequence[Bar],
the project's standard price-series type (no pandas), consistent with
market_structure/ and smc/. RegimeType is deliberately separate from
market_structure.structure_models.StructureTrend: that enum is swing-based
(built from confirmed structural pivots), this one is purely statistical
(built from bar-to-bar return autocorrelation).
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from core.models import Bar, Timeframe
from smc.displacement import calculate_tr_and_atr


class RegimeType(Enum):
    """Classifies how a price series has been behaving over its analysis window."""

    TRENDING = "TRENDING"
    MEAN_REVERTING = "MEAN_REVERTING"
    RANGING = "RANGING"


@dataclass(frozen=True)
class VolatilityRegime:
    """The current ATR reading and how it ranks against its own recent history."""

    atr: float
    atr_percentile: float
    bucket: Literal["low", "normal", "high"]


@dataclass(frozen=True)
class MoveStatistics:
    """Summary statistics of bar-to-bar close moves over an analysis window."""

    mean_move: float
    median_move: float
    stdev_move: float
    up_bar_pct: float
    down_bar_pct: float


@dataclass(frozen=True)
class RegimeSummary:
    """Combined regime classification for one symbol/timeframe/window."""

    symbol: str
    timeframe: Timeframe
    regime: RegimeType
    autocorrelation_lag1: float
    volatility: VolatilityRegime
    moves: MoveStatistics
    window_bars: int


def _bar_returns(bars: Sequence[Bar]) -> list[float]:
    """Bar-to-bar close returns (close[i] - close[i-1]), one shorter than bars."""
    return [bars[i].close - bars[i - 1].close for i in range(1, len(bars))]


def compute_autocorrelation(bars: Sequence[Bar], lag: int = 1) -> float:
    """Computes the lag-N autocorrelation of bar-to-bar close returns.

    Positive values suggest trending behavior (a move tends to be followed
    by a same-direction move); negative values suggest mean-reverting
    behavior (a move tends to be followed by a reversal).

    Args:
        bars: Chronologically ordered bars.
        lag: The lag (in bars) to correlate returns against.

    Returns:
        The Pearson correlation coefficient between returns[t] and
        returns[t+lag], in [-1, 1]. 0.0 if there are fewer than lag + 2
        returns (not enough data) or if either series has zero variance.
    """
    returns = _bar_returns(bars)
    if len(returns) <= lag + 1:
        return 0.0

    x = returns[: len(returns) - lag]
    y = returns[lag:]

    mean_x = statistics.mean(x)
    mean_y = statistics.mean(y)
    covariance = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y, strict=True))
    variance_x = sum((xi - mean_x) ** 2 for xi in x)
    variance_y = sum((yi - mean_y) ** 2 for yi in y)

    denominator = (variance_x * variance_y) ** 0.5
    if denominator == 0.0:
        return 0.0
    return float(covariance / denominator)


def classify_regime(
    autocorr: float, trend_threshold: float = 0.1, revert_threshold: float = -0.1
) -> RegimeType:
    """Classifies a regime from its lag-1 autocorrelation.

    Args:
        autocorr: compute_autocorrelation()'s output.
        trend_threshold: autocorr at/above this is classified TRENDING.
        revert_threshold: autocorr at/below this is classified MEAN_REVERTING.

    Returns:
        TRENDING, MEAN_REVERTING, or RANGING (the band between the two thresholds).
    """
    if autocorr >= trend_threshold:
        return RegimeType.TRENDING
    if autocorr <= revert_threshold:
        return RegimeType.MEAN_REVERTING
    return RegimeType.RANGING


def compute_volatility_regime(
    bars: Sequence[Bar], lookback: int = 100, atr_period: int = 14
) -> VolatilityRegime:
    """Classifies the current ATR against its own recent history.

    Args:
        bars: Chronologically ordered bars.
        lookback: Number of trailing ATR readings compared against.
        atr_period: Wilder's ATR smoothing period (see smc.displacement.calculate_tr_and_atr).

    Returns:
        A VolatilityRegime with the latest ATR value, its percentile rank
        within the trailing `lookback` ATR readings, and a low/normal/high
        bucket (below 33rd percentile / between / above 67th percentile).
        atr=0.0, atr_percentile=0.0, bucket="normal" if bars is empty.
    """
    if not bars:
        return VolatilityRegime(atr=0.0, atr_percentile=0.0, bucket="normal")

    _, atr_values = calculate_tr_and_atr(bars, atr_period)
    current_atr = atr_values[-1]
    window = atr_values[-lookback:]

    if len(window) <= 1:
        return VolatilityRegime(atr=current_atr, atr_percentile=50.0, bucket="normal")

    rank = sum(1 for value in window if value <= current_atr)
    percentile = 100.0 * rank / len(window)

    bucket: Literal["low", "normal", "high"]
    if percentile < 33.0:
        bucket = "low"
    elif percentile > 67.0:
        bucket = "high"
    else:
        bucket = "normal"

    return VolatilityRegime(atr=current_atr, atr_percentile=percentile, bucket=bucket)


def compute_move_statistics(bars: Sequence[Bar]) -> MoveStatistics:
    """Computes summary statistics of bar-to-bar close moves.

    Quantifies the "market doesn't move in one direction forever" intuition:
    the balance of up vs down bars and the typical move size/spread.

    Args:
        bars: Chronologically ordered bars.

    Returns:
        Mean/median/stdev of bar-to-bar close changes, plus the percentage
        of bars that closed up vs down. All zero if fewer than 2 bars.
    """
    moves = _bar_returns(bars)
    if not moves:
        return MoveStatistics(
            mean_move=0.0, median_move=0.0, stdev_move=0.0, up_bar_pct=0.0, down_bar_pct=0.0
        )

    up_count = sum(1 for move in moves if move > 0)
    down_count = sum(1 for move in moves if move < 0)
    total = len(moves)

    return MoveStatistics(
        mean_move=statistics.mean(moves),
        median_move=statistics.median(moves),
        stdev_move=statistics.stdev(moves) if total > 1 else 0.0,
        up_bar_pct=100.0 * up_count / total,
        down_bar_pct=100.0 * down_count / total,
    )


def analyze_regime(
    bars: Sequence[Bar], symbol: str, timeframe: Timeframe, window_bars: int = 200
) -> RegimeSummary:
    """Top-level orchestrator: analyzes the trailing `window_bars` of `bars`.

    Args:
        bars: Chronologically ordered bars (only the most recent window_bars
            are analyzed).
        symbol: Trading instrument symbol (carried through for reporting).
        timeframe: Bar timeframe (carried through for reporting).
        window_bars: Number of most-recent bars to analyze. 0 or negative
            analyzes the full sequence.

    Returns:
        A RegimeSummary combining autocorrelation-based regime
        classification, volatility regime, and move statistics.
    """
    window = list(bars[-window_bars:]) if window_bars > 0 else list(bars)
    autocorr = compute_autocorrelation(window)
    return RegimeSummary(
        symbol=symbol,
        timeframe=timeframe,
        regime=classify_regime(autocorr),
        autocorrelation_lag1=autocorr,
        volatility=compute_volatility_regime(window),
        moves=compute_move_statistics(window),
        window_bars=len(window),
    )
