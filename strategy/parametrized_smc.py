"""ParametrizedSMCStrategy: a single, configuration-driven SMC strategy used
as the building block for Guarded Pattern Discovery (see
research/pattern_discovery.py).

Unlike every other strategy module in this package (one hand-written class
per trading idea), this strategy is deliberately generic: one
PatternCandidateConfig selects a single point in a 6-dimensional search
space (Order Block direction x FVG requirement x liquidity-sweep
requirement x entry point x take-profit R x trend filter), and the class
itself contains no strategy-specific logic beyond "read the already-computed
SMC pipeline state (market_state.smc_state / market_state.structure_state)
and apply the selected filters/entry rule." It detects nothing itself,
exactly like OrderBlockRetestStrategy (whose OB-touch/entry/stop logic this
reuses as the OB_EDGE case).

Entry point price convention (mirrors OrderBlockRetestStrategy's "near/
shallow edge" choice for OB_EDGE): the entry always sits at the edge of the
zone closest to a shallow retracement, i.e. the edge price would touch
first on the smallest possible pullback:
- OB_EDGE: ob.high (bullish) / ob.low (bearish) -- identical to
  OrderBlockRetestStrategy.
- OB_MID:  midpoint of the OB's [low, high] range.
- FVG_EDGE: fvg.upper_price (bullish) / fvg.lower_price (bearish) -- the
  gap edge nearest to price approaching from the trend side.

entry_point=FVG_EDGE structurally requires an FVG to price the entry from,
so PatternCandidateConfig forces require_fvg=True whenever entry_point is
FVG_EDGE (validated in __post_init__, not silently coerced) -- per the
approved Guarded Pattern Discovery search-space design, this collapses the
288-cell grid (2x2x2x3x4x3) to fewer unique, non-contradictory candidates by
construction rather than generating and then discarding invalid combinations.
"""

import uuid
from dataclasses import dataclass
from enum import Enum

from core.models import SignalDirection
from core.validation import require_positive
from market_structure.structure_models import MarketState, StructureTrend
from smc.fvg import FairValueGap, FVGDirection
from smc.liquidity import LiquidityType
from smc.order_block import OBDirection, OrderBlock
from strategy.diagnostics import RejectionReason, StrategyDiagnostics
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup


class EntryPoint(str, Enum):
    """Where the pending limit order is priced, relative to the matched OB/FVG."""

    OB_EDGE = "ob_edge"
    OB_MID = "ob_mid"
    FVG_EDGE = "fvg_edge"


class TrendFilterMode(str, Enum):
    """How market_state.structure_state.trend gates the candidate's OB direction."""

    ALIGNED = "aligned"
    COUNTER = "counter"
    NONE = "none"


@dataclass(frozen=True)
class PatternCandidateConfig:
    """One point in the Guarded Pattern Discovery search space.

    Attributes:
        candidate_id: Stable, human-readable identifier encoding all 6
            dimensions (used as TradeSetup.strategy_name and as the key
            results are grouped/reported by in research/pattern_discovery.py).
        ob_direction: Which Order Block direction this candidate trades
            (BULLISH -> BUY setups only, BEARISH -> SELL setups only).
        require_fvg: Whether an unmitigated FVG matching ob_direction must
            also be present (in addition to the touched OB) for a setup.
        require_liquidity_sweep: Whether the opposite-side liquidity must
            already be swept (mirrors BullishContinuationStrategy's Rule 6).
        entry_point: Where the entry price is taken from.
        take_profit_r: Fixed reward:risk multiple applied to the stop distance.
        trend_filter: Whether market_state.structure_state.trend must align
            with, oppose, or be ignored relative to ob_direction.
    """

    candidate_id: str
    ob_direction: OBDirection
    require_fvg: bool
    require_liquidity_sweep: bool
    entry_point: EntryPoint
    take_profit_r: float
    trend_filter: TrendFilterMode

    def __post_init__(self) -> None:
        """Validates parameter ranges and the FVG_EDGE/require_fvg invariant.

        Raises:
            ValueError: If take_profit_r is not strictly positive, or if
                entry_point is FVG_EDGE while require_fvg is False (no FVG
                is guaranteed to exist to price the entry from in that case
                -- construct this candidate with require_fvg=True instead of
                relying on a silent fallback).
        """
        require_positive(self.take_profit_r, "take_profit_r")
        if self.entry_point == EntryPoint.FVG_EDGE and not self.require_fvg:
            raise ValueError(
                "entry_point=FVG_EDGE requires require_fvg=True -- there is no "
                "guaranteed FVG to price the entry from otherwise. Construct this "
                "candidate with require_fvg=True."
            )


def _fvg_distance(fvg: FairValueGap, price: float) -> float:
    """Distance from price to the FVG zone; 0.0 if price is inside it."""
    if fvg.lower_price <= price <= fvg.upper_price:
        return 0.0
    return min(abs(price - fvg.lower_price), abs(price - fvg.upper_price))


def _select_nearest_fvg(
    fair_value_gaps: list[FairValueGap], direction: FVGDirection, price: float
) -> FairValueGap | None:
    """Selects the unmitigated FVG of the given direction nearest to price.

    Same distance-then-recency tie-break as continuation.py's
    _select_best_fvg (Bug #10), but with no proximity threshold -- the
    Guarded Pattern Discovery search space has no proximity dimension, so
    this always returns the single closest matching FVG if any exists.
    """
    candidates = [
        fvg for fvg in fair_value_gaps if fvg.direction == direction and not fvg.is_mitigated
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda fvg: (_fvg_distance(fvg, price), -fvg.end_index))


class ParametrizedSMCStrategy(TradeSetupStrategy):
    """Evaluates one PatternCandidateConfig's point in the search space.

    Evaluates whether the current bar satisfies the candidate's selected
    OB/FVG/liquidity/trend filters, without mutating MarketState.
    """

    def __init__(self, config: PatternCandidateConfig) -> None:
        """Initializes the strategy for a single candidate configuration.

        Args:
            config: The PatternCandidateConfig this instance evaluates.
        """
        self.config = config
        self.diagnostics = StrategyDiagnostics()
        self._used_ob_ids: set[str] = set()

    def reset(self) -> None:
        """Resets the once-per-OB usage memory and diagnostics counters."""
        self.diagnostics.reset()
        self._used_ob_ids.clear()

    def _reject(self, reason: RejectionReason) -> None:
        """Records a rejection reason and returns None (for use in `return self._reject(...)`)."""
        self.diagnostics.record_rejection(reason)
        return None

    def _is_touched(self, ob: OrderBlock, bar_low: float, bar_high: float) -> bool:
        """Whether the current bar's range crosses the OB's relevant edge."""
        edge = ob.high if ob.direction == OBDirection.BULLISH else ob.low
        return bar_low <= edge <= bar_high

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates the candidate's filters against the current bar.

        Gate order:
        1. A latest closed bar exists.
        2. Trend filter (if trend_filter != NONE).
        3. At least one untraded Order Block of ob_direction exists and is touched.
        4. FVG requirement (if require_fvg, or entry_point == FVG_EDGE).
        5. Liquidity sweep requirement (if require_liquidity_sweep).
        6. Positive risk distance.
        """
        self.diagnostics.record_evaluation()
        cfg = self.config

        latest_bar = market_state.get_latest_bar()
        if latest_bar is None:
            return self._reject(RejectionReason.NO_LATEST_BAR)

        direction = (
            SignalDirection.BUY if cfg.ob_direction == OBDirection.BULLISH else SignalDirection.SELL
        )

        # --- Trend Filter ---
        if cfg.trend_filter != TrendFilterMode.NONE:
            is_bullish_ob = cfg.ob_direction == OBDirection.BULLISH
            if cfg.trend_filter == TrendFilterMode.ALIGNED:
                wanted_trend = StructureTrend.BULLISH if is_bullish_ob else StructureTrend.BEARISH
            else:  # COUNTER
                wanted_trend = StructureTrend.BEARISH if is_bullish_ob else StructureTrend.BULLISH
            if market_state.structure_state.trend != wanted_trend:
                return self._reject(RejectionReason.NO_TREND)

        # --- Order Block Check (direction-filtered, once-per-OB, Bug #10 recency tiebreak) ---
        direction_obs = [
            ob for ob in market_state.smc_state.order_blocks if ob.direction == cfg.ob_direction
        ]
        if not direction_obs:
            return self._reject(RejectionReason.NO_ORDER_BLOCKS)

        candidates = [ob for ob in direction_obs if ob.id not in self._used_ob_ids]
        if not candidates:
            return self._reject(RejectionReason.OB_ALREADY_USED)

        touched = [ob for ob in candidates if self._is_touched(ob, latest_bar.low, latest_bar.high)]
        if not touched:
            return self._reject(RejectionReason.NO_TOUCH_DETECTED)

        matching_ob = max(touched, key=lambda ob: ob.bar_index)

        # --- FVG Requirement ---
        matching_fvg: FairValueGap | None = None
        if cfg.require_fvg:
            fvg_direction = (
                FVGDirection.BULLISH if cfg.ob_direction == OBDirection.BULLISH else FVGDirection.BEARISH
            )
            matching_fvg = _select_nearest_fvg(
                market_state.smc_state.fair_value_gaps, fvg_direction, latest_bar.close
            )
            if matching_fvg is None:
                return self._reject(RejectionReason.NO_MATCHING_FVG)

        # --- Liquidity Sweep Requirement ---
        if cfg.require_liquidity_sweep:
            wanted_liquidity = (
                LiquidityType.SELL_SIDE
                if cfg.ob_direction == OBDirection.BULLISH
                else LiquidityType.BUY_SIDE
            )
            swept = any(
                lvl.type == wanted_liquidity and lvl.is_swept
                for lvl in market_state.smc_state.liquidity_levels
            )
            if not swept:
                return self._reject(RejectionReason.LIQUIDITY_NOT_SWEPT)

        # --- Calculate Entry/Stop/Target ---
        if cfg.entry_point == EntryPoint.OB_EDGE:
            entry = matching_ob.high if direction == SignalDirection.BUY else matching_ob.low
        elif cfg.entry_point == EntryPoint.OB_MID:
            entry = (matching_ob.high + matching_ob.low) / 2
        else:  # FVG_EDGE (matching_fvg is guaranteed non-None: require_fvg forced True)
            assert matching_fvg is not None
            entry = matching_fvg.upper_price if direction == SignalDirection.BUY else matching_fvg.lower_price

        sl = matching_ob.low if direction == SignalDirection.BUY else matching_ob.high
        risk_dist = (entry - sl) if direction == SignalDirection.BUY else (sl - entry)

        if risk_dist <= 0.0:
            return self._reject(RejectionReason.NON_POSITIVE_RISK)

        reward_dist = risk_dist * cfg.take_profit_r
        tp = entry + reward_dist if direction == SignalDirection.BUY else entry - reward_dist

        self._used_ob_ids.add(matching_ob.id)

        direction_label = "Bullish" if direction == SignalDirection.BUY else "Bearish"
        unique_id = uuid.uuid4().hex[:8]
        timestamp = latest_bar.timestamp
        ts_str = timestamp.strftime("%Y%m%d_%H%M%S_%f")
        setup_id = (
            f"setup_{cfg.candidate_id}_{market_state.symbol}_{market_state.timeframe.value}_"
            f"{unique_id}_{ts_str}"
        )

        self.diagnostics.record_setup_generated()
        return TradeSetup(
            setup_id=setup_id,
            symbol=market_state.symbol,
            timeframe=market_state.timeframe,
            direction=direction,
            entry_zone=(round(entry, 5), round(entry, 5)),
            stop_zone=(round(sl, 5), round(sl, 5)),
            target_zone=(round(tp, 5), round(tp, 5)),
            confidence_score=1.0,
            confluence=[
                f"{direction_label} Order Block ({matching_ob.id})",
                f"Entry: {cfg.entry_point.value}",
                f"FVG required: {cfg.require_fvg}",
                f"Liquidity sweep required: {cfg.require_liquidity_sweep}",
                f"Trend filter: {cfg.trend_filter.value}",
                f"Take profit: {cfg.take_profit_r}R",
            ],
            trigger_reason=(
                f"Pattern candidate {cfg.candidate_id} matched at {entry:.5f} "
                f"(OB {matching_ob.id})"
            ),
            invalidations=[
                "Price closes through the Order Block's far edge",
                "Price breaches Stop Loss zone",
            ],
            related_structure_break=None,
            related_order_block=matching_ob,
            related_fvg=matching_fvg,
            timestamp=timestamp,
            strategy_name=cfg.candidate_id,
        )
