"""Unit tests for analysis/bias_score.py."""

from datetime import datetime

import pytest

from analysis.bias_score import BiasScorer
from core.models import SignalDirection, Timeframe
from market_structure.structure_models import MarketState, StructureState, StructureTrend
from research.regime_analysis import MoveStatistics, RegimeSummary, RegimeType, VolatilityRegime
from smc.fvg import FairValueGap, FVGDirection
from smc.order_block import OBDirection, OrderBlock
from smc.premium_discount import PremiumDiscountZone, ZoneType
from market_structure.structure_models import SMCState

_START = datetime(2026, 1, 1)


def _order_block(index: int, direction: OBDirection, mitigated: bool = False) -> OrderBlock:
    return OrderBlock(
        id=f"ob_{index}_{direction.value.lower()}",
        bar_index=index,
        high=101.0,
        low=99.0,
        direction=direction,
        timestamp=_START,
        is_mitigated=mitigated,
    )


def _fvg(index: int, direction: FVGDirection, mitigated: bool = False) -> FairValueGap:
    return FairValueGap(
        id=f"fvg_{index}_{direction.value.lower()}",
        start_index=index,
        end_index=index + 2,
        upper_price=101.0,
        lower_price=100.0,
        direction=direction,
        timestamp=_START,
        is_mitigated=mitigated,
    )


def _market_state(
    trend: StructureTrend,
    confidence: float,
    order_blocks: list[OrderBlock] | None = None,
    fvgs: list[FairValueGap] | None = None,
    zone: PremiumDiscountZone | None = None,
) -> MarketState:
    return MarketState(
        symbol="EURUSD",
        timeframe=Timeframe.M5,
        structure_state=StructureState(trend=trend, confidence=confidence),
        smc_state=SMCState(order_blocks=order_blocks or [], fair_value_gaps=fvgs or []),
        premium_discount_zone=zone,
    )


def _regime_summary(regime_type: RegimeType) -> RegimeSummary:
    return RegimeSummary(
        symbol="EURUSD",
        timeframe=Timeframe.M5,
        regime=regime_type,
        autocorrelation_lag1=0.0,
        volatility=VolatilityRegime(atr=1.0, atr_percentile=50.0, bucket="normal"),
        moves=MoveStatistics(0.0, 0.0, 0.0, 50.0, 50.0),
        window_bars=200,
    )


class TestBiasScorerWeightValidation:
    def test_raises_when_weights_do_not_sum_to_one(self) -> None:
        with pytest.raises(ValueError, match="sum to 1.0"):
            BiasScorer(structure_weight=0.5, smc_weight=0.5, zone_weight=0.5, regime_weight=0.5)

    def test_accepts_default_weights(self) -> None:
        BiasScorer()  # must not raise


class TestBiasScorerDirectionalLean:
    def test_strong_bullish_confluence_produces_high_buy_probability(self) -> None:
        market_state = _market_state(
            trend=StructureTrend.BULLISH,
            confidence=0.8,
            order_blocks=[_order_block(0, OBDirection.BULLISH), _order_block(1, OBDirection.BULLISH)],
            zone=PremiumDiscountZone(high=110.0, low=90.0, equilibrium=100.0, current_price=95.0, zone=ZoneType.DISCOUNT),
        )

        score = BiasScorer().score(market_state)

        assert score.probability > 0.7
        assert score.direction == SignalDirection.BUY

    def test_weak_conflicting_signals_stay_near_neutral(self) -> None:
        market_state = _market_state(
            trend=StructureTrend.BEARISH,
            confidence=0.2,
            order_blocks=[_order_block(0, OBDirection.BULLISH), _order_block(1, OBDirection.BEARISH)],
            zone=PremiumDiscountZone(high=110.0, low=90.0, equilibrium=100.0, current_price=100.0, zone=ZoneType.EQUILIBRIUM),
        )

        score = BiasScorer().score(market_state)

        assert score.probability == pytest.approx(0.5, abs=0.05)
        assert score.direction is None

    def test_range_trend_and_no_smc_signals_is_fully_neutral(self) -> None:
        market_state = _market_state(trend=StructureTrend.RANGE, confidence=0.0)

        score = BiasScorer().score(market_state)

        assert score.probability == 0.5
        assert score.direction is None
        assert score.confidence == 0.0


class TestBiasScorerRegimeFactor:
    def test_trending_regime_sharpens_and_ranging_dampens(self) -> None:
        market_state = _market_state(
            trend=StructureTrend.BULLISH,
            confidence=0.6,
            order_blocks=[_order_block(0, OBDirection.BULLISH)],
        )
        scorer = BiasScorer()

        no_regime = scorer.score(market_state)
        trending = scorer.score(market_state, regime=_regime_summary(RegimeType.TRENDING))
        ranging = scorer.score(market_state, regime=_regime_summary(RegimeType.RANGING))

        assert trending.probability > no_regime.probability > ranging.probability
        assert trending.confidence > ranging.confidence

    def test_regime_factor_appears_in_factors_list_only_when_provided(self) -> None:
        market_state = _market_state(trend=StructureTrend.BULLISH, confidence=0.5)
        scorer = BiasScorer()

        without_regime = scorer.score(market_state)
        with_regime = scorer.score(market_state, regime=_regime_summary(RegimeType.TRENDING))

        assert "regime_alignment" not in {f.name for f in without_regime.factors}
        assert "regime_alignment" in {f.name for f in with_regime.factors}
