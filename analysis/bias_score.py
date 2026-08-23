"""A probabilistic (not deterministic) up/down directional bias score.

Combines existing SMC/structure signals already computed on MarketState.
Decision-support only -- deliberately NOT wired into StrategyEngine.run() or
TradeManager. A human (or a future, separately-reviewed strategy) reads
BiasScore; nothing here places or sizes a trade.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

from core.models import SignalDirection, Timeframe
from market_structure.structure_models import MarketState, StructureTrend
from research.regime_analysis import RegimeSummary, RegimeType
from smc.fvg import FVGDirection
from smc.order_block import OBDirection
from smc.premium_discount import ZoneType

_DEFAULT_NEUTRAL_BAND = (0.45, 0.55)


@dataclass(frozen=True)
class BiasFactor:
    """One scored input that fed into a BiasScore, kept for transparency."""

    name: str
    direction: SignalDirection | None
    weight: float
    contribution: float


@dataclass(frozen=True)
class BiasScore:
    """A probabilistic directional lean for one symbol/timeframe at a point in time."""

    symbol: str
    timeframe: Timeframe
    direction: SignalDirection | None
    probability: float
    confidence: float
    factors: list[BiasFactor] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


def _direction_from_score(score: float) -> SignalDirection | None:
    if score > 0:
        return SignalDirection.BUY
    if score < 0:
        return SignalDirection.SELL
    return None


def _structure_score(market_state: MarketState) -> float:
    """Directional score in [-1, 1] from the existing structure confidence."""
    trend = market_state.structure_state.trend
    confidence = market_state.structure_state.confidence
    if trend == StructureTrend.BULLISH:
        return confidence
    if trend == StructureTrend.BEARISH:
        return -confidence
    return 0.0


def _smc_score(market_state: MarketState) -> float:
    """Net directional score in [-1, 1] from unmitigated order blocks + FVGs."""
    bullish = 0
    bearish = 0
    for ob in market_state.smc_state.order_blocks:
        if ob.is_mitigated:
            continue
        if ob.direction == OBDirection.BULLISH:
            bullish += 1
        else:
            bearish += 1
    for fvg in market_state.smc_state.fair_value_gaps:
        if fvg.is_mitigated:
            continue
        if fvg.direction == FVGDirection.BULLISH:
            bullish += 1
        else:
            bearish += 1

    total = bullish + bearish
    if total == 0:
        return 0.0
    return (bullish - bearish) / total


def _zone_score(market_state: MarketState) -> float:
    """Directional score in {-1, 0, 1} from the premium/discount zone."""
    zone = market_state.premium_discount_zone
    if zone is None:
        return 0.0
    if zone.zone == ZoneType.DISCOUNT:
        return 1.0
    if zone.zone == ZoneType.PREMIUM:
        return -1.0
    return 0.0


class BiasScorer:
    """Combines structure/SMC/zone (and optionally regime) signals into a BiasScore."""

    def __init__(
        self,
        structure_weight: float = 0.35,
        smc_weight: float = 0.35,
        zone_weight: float = 0.15,
        regime_weight: float = 0.15,
        neutral_band: tuple[float, float] = _DEFAULT_NEUTRAL_BAND,
    ) -> None:
        """Initializes the BiasScorer.

        Args:
            structure_weight: Weight of the market-structure trend/confidence factor.
            smc_weight: Weight of the unmitigated order-block/FVG net-direction factor.
            zone_weight: Weight of the premium/discount zone factor.
            regime_weight: Weight reserved for the optional regime factor (see
                score()'s `regime` argument) -- sharpens the combined score
                when TRENDING, dampens it when RANGING/MEAN_REVERTING.
            neutral_band: (low, high) probability range treated as "no lean"
                (BiasScore.direction is None inside this band).

        Raises:
            ValueError: If the four weights don't sum to 1.0 (within a small
                floating-point tolerance).
        """
        total_weight = structure_weight + smc_weight + zone_weight + regime_weight
        if abs(total_weight - 1.0) > 1e-9:
            raise ValueError(
                f"BiasScorer weights must sum to 1.0, got {total_weight!r} "
                f"(structure={structure_weight}, smc={smc_weight}, zone={zone_weight}, "
                f"regime={regime_weight})."
            )
        self.structure_weight = structure_weight
        self.smc_weight = smc_weight
        self.zone_weight = zone_weight
        self.regime_weight = regime_weight
        self.neutral_band = neutral_band

    def score(self, market_state: MarketState, regime: RegimeSummary | None = None) -> BiasScore:
        """Computes a BiasScore for market_state's current state.

        Args:
            market_state: An already-populated MarketState.
            regime: Optional research.regime_analysis.RegimeSummary for the
                same symbol/timeframe -- if given, TRENDING sharpens the
                combined score, RANGING/MEAN_REVERTING dampens it toward 0.5.

        Returns:
            A BiasScore with probability in [0, 1] (>0.5 BUY-leaning, <0.5
            SELL-leaning), direction=None inside neutral_band, and the
            individual factors that produced it.
        """
        structure_score = _structure_score(market_state)
        smc_score = _smc_score(market_state)
        zone_score = _zone_score(market_state)

        factors = [
            BiasFactor(
                name="structure_trend",
                direction=_direction_from_score(structure_score),
                weight=self.structure_weight,
                contribution=self.structure_weight * structure_score,
            ),
            BiasFactor(
                name="smc_order_flow",
                direction=_direction_from_score(smc_score),
                weight=self.smc_weight,
                contribution=self.smc_weight * smc_score,
            ),
            BiasFactor(
                name="premium_discount_zone",
                direction=_direction_from_score(zone_score),
                weight=self.zone_weight,
                contribution=self.zone_weight * zone_score,
            ),
        ]

        base_score = sum(f.contribution for f in factors)
        final_score = base_score

        if regime is not None:
            if regime.regime == RegimeType.TRENDING:
                final_score = base_score * (1.0 + self.regime_weight)
            elif regime.regime in (RegimeType.RANGING, RegimeType.MEAN_REVERTING):
                final_score = base_score * (1.0 - self.regime_weight)
            factors.append(
                BiasFactor(
                    name="regime_alignment",
                    direction=None,
                    weight=self.regime_weight,
                    contribution=final_score - base_score,
                )
            )

        final_score = max(-1.0, min(1.0, final_score))
        probability = max(0.0, min(1.0, 0.5 + 0.5 * final_score))
        confidence = abs(probability - 0.5) * 2.0

        low, high = self.neutral_band
        direction = None if low <= probability <= high else _direction_from_score(probability - 0.5)

        return BiasScore(
            symbol=market_state.symbol,
            timeframe=market_state.timeframe,
            direction=direction,
            probability=probability,
            confidence=confidence,
            factors=factors,
        )
