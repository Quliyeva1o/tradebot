"""Continuation strategies implementation."""

import uuid
from datetime import datetime

from core.models import SignalDirection
from market_structure.structure_models import BreakType, MarketState, StructureTrend
from market_structure.swing_models import SwingType
from smc.fvg import FVGDirection
from smc.liquidity import LiquidityType
from smc.order_block import OBDirection
from smc.premium_discount import ZoneType
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup


class BullishContinuationStrategy(TradeSetupStrategy):
    """Bullish SMC continuation strategy module.

    Evaluates whether the MarketState meets all required criteria for a bullish
    continuation setup candidate, without mutating state.
    """

    def __init__(
        self,
        pip_size: float = 0.0001,
        lookback_bars: int = 20,
        fvg_proximity_pips: float = 50.0,
        stop_buffer_pips: float = 5.0,
    ) -> None:
        """Initializes the BullishContinuationStrategy with parameters.

        Args:
            pip_size: Price value of a single pip.
            lookback_bars: Lookback window for recent displacement.
            fvg_proximity_pips: Maximum proximity limit to FVG in pips.
            stop_buffer_pips: Stop loss buffer limit in pips.
        """
        self.pip_size = pip_size
        self.lookback_bars = lookback_bars
        self.fvg_proximity_pips = fvg_proximity_pips
        self.stop_buffer_pips = stop_buffer_pips

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates rules for bullish continuation.

        Required checks:
        1. Trend == Bullish
        2. Latest Structure Break == BOS
        3. Break broke a HIGH
        4. Current price inside ACTIVE bullish Order Block
        5. Nearby ACTIVE bullish FVG
        6. Sell-side liquidity already swept
        7. Recent bullish displacement exists
        """
        # --- Rule 1: Trend Check ---
        if market_state.structure_state.trend != StructureTrend.BULLISH:
            return None

        # --- Rule 8: Premium / Discount Zone Check ---
        if market_state.premium_discount_zone is None:
            return None
        if market_state.premium_discount_zone.zone != ZoneType.DISCOUNT:
            return None

        # --- Rule 2 & 3: Break Check ---
        breaks = market_state.structure_state.breaks_history
        if not breaks:
            return None
        last_break = breaks[-1]
        if last_break.break_type != BreakType.BOS:
            return None
        if last_break.broken_swing.type != SwingType.HIGH:
            return None

        # Get latest closed bar
        latest_bar = market_state.get_latest_bar()
        if latest_bar is None:
            return None

        # --- Rule 4: Order Block Check ---
        matching_ob = None
        for ob in market_state.smc_state.order_blocks:
            if ob.direction == OBDirection.BULLISH and not ob.is_mitigated:
                if ob.low <= latest_bar.close <= ob.high:
                    matching_ob = ob
                    break
        if matching_ob is None:
            return None

        # --- Rule 5: FVG Check ---
        matching_fvg = None
        proximity_threshold = self.fvg_proximity_pips * self.pip_size
        for fvg in market_state.smc_state.fair_value_gaps:
            if fvg.direction == FVGDirection.BULLISH and not fvg.is_mitigated:
                # Calculate distance to FVG boundaries
                if fvg.lower_price <= latest_bar.close <= fvg.upper_price:
                    matching_fvg = fvg
                    break
                dist = min(
                    abs(latest_bar.close - fvg.lower_price),
                    abs(latest_bar.close - fvg.upper_price),
                )
                if dist <= proximity_threshold:
                    matching_fvg = fvg
                    break
        if matching_fvg is None:
            return None

        # --- Rule 6: Liquidity Sweep Check ---
        sellside_swept = any(
            lvl.type == LiquidityType.SELL_SIDE and lvl.is_swept
            for lvl in market_state.smc_state.liquidity_levels
        )
        if not sellside_swept:
            return None

        # --- Rule 7: Displacement Check ---
        latest_idx = len(market_state.bars) - 1
        displacement_confirmed = any(
            d.direction == "BULLISH" and (latest_idx - d.bar_index) <= self.lookback_bars
            for d in market_state.smc_state.displacements
        )
        if not displacement_confirmed:
            return None

        # --- Calculate Zones ---
        entry_zone = (round(matching_ob.low, 5), round(matching_ob.high, 5))
        stop_buffer = self.stop_buffer_pips * self.pip_size
        stop_zone = (round(matching_ob.low - stop_buffer, 5), round(matching_ob.low, 5))

        latest_high = market_state.swing_graph.get_latest_high()
        if latest_high is not None:
            target_price = latest_high.price
        else:
            target_price = matching_ob.high + 2.0 * (matching_ob.high - stop_zone[0])
        target_zone = (round(target_price, 5), round(target_price + stop_buffer, 5))

        # Generate Setup ID
        timestamp = datetime.now()
        ts_str = timestamp.strftime("%Y%m%d_%H%M%S_%f")
        unique_id = uuid.uuid4().hex[:8]
        setup_id = f"setup_bullish_continuation_{market_state.symbol}_{market_state.timeframe.value}_{unique_id}_{ts_str}"

        return TradeSetup(
            setup_id=setup_id,
            symbol=market_state.symbol,
            timeframe=market_state.timeframe,
            direction=SignalDirection.BUY,
            entry_zone=entry_zone,
            stop_zone=stop_zone,
            target_zone=target_zone,
            confidence_score=market_state.structure_state.confidence,
            confluence=[
                "Bullish Trend",
                "Bullish BOS",
                "Inside Bullish OB",
                "Bullish FVG Proximity",
                "Sell-side Liquidity Swept",
                "Bullish Displacement",
            ],
            trigger_reason=(
                f"Bullish continuation setup at {matching_ob.id} confirmed by "
                f"break {last_break.break_id} and nearby FVG {matching_fvg.id}"
            ),
            invalidations=[
                "Price closes below Order Block low",
                "Price breaches Stop Loss zone",
            ],
            related_structure_break=last_break,
            related_order_block=matching_ob,
            related_fvg=matching_fvg,
            timestamp=timestamp,
        )


class BearishContinuationStrategy(TradeSetupStrategy):
    """Bearish SMC continuation strategy module.

    Evaluates whether the MarketState meets all required criteria for a bearish
    continuation setup candidate, without mutating state.
    """

    def __init__(
        self,
        pip_size: float = 0.0001,
        lookback_bars: int = 20,
        fvg_proximity_pips: float = 50.0,
        stop_buffer_pips: float = 5.0,
    ) -> None:
        """Initializes the BearishContinuationStrategy with parameters.

        Args:
            pip_size: Price value of a single pip.
            lookback_bars: Lookback window for recent displacement.
            fvg_proximity_pips: Maximum proximity limit to FVG in pips.
            stop_buffer_pips: Stop loss buffer limit in pips.
        """
        self.pip_size = pip_size
        self.lookback_bars = lookback_bars
        self.fvg_proximity_pips = fvg_proximity_pips
        self.stop_buffer_pips = stop_buffer_pips

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates rules for bearish continuation.

        Required checks:
        1. Trend == Bearish
        2. Latest Structure Break == BOS
        3. Break broke a LOW
        4. Current price inside ACTIVE bearish Order Block
        5. Nearby ACTIVE bearish FVG
        6. Buy-side liquidity already swept
        7. Recent bearish displacement exists
        """
        # --- Rule 1: Trend Check ---
        if market_state.structure_state.trend != StructureTrend.BEARISH:
            return None

        # --- Rule 8: Premium / Discount Zone Check ---
        if market_state.premium_discount_zone is None:
            return None
        if market_state.premium_discount_zone.zone != ZoneType.PREMIUM:
            return None

        # --- Rule 2 & 3: Break Check ---
        breaks = market_state.structure_state.breaks_history
        if not breaks:
            return None
        last_break = breaks[-1]
        if last_break.break_type != BreakType.BOS:
            return None
        if last_break.broken_swing.type != SwingType.LOW:
            return None

        # Get latest closed bar
        latest_bar = market_state.get_latest_bar()
        if latest_bar is None:
            return None

        # --- Rule 4: Order Block Check ---
        matching_ob = None
        for ob in market_state.smc_state.order_blocks:
            if ob.direction == OBDirection.BEARISH and not ob.is_mitigated:
                if ob.low <= latest_bar.close <= ob.high:
                    matching_ob = ob
                    break
        if matching_ob is None:
            return None

        # --- Rule 5: FVG Check ---
        matching_fvg = None
        proximity_threshold = self.fvg_proximity_pips * self.pip_size
        for fvg in market_state.smc_state.fair_value_gaps:
            if fvg.direction == FVGDirection.BEARISH and not fvg.is_mitigated:
                # Calculate distance to FVG boundaries
                if fvg.lower_price <= latest_bar.close <= fvg.upper_price:
                    matching_fvg = fvg
                    break
                dist = min(
                    abs(latest_bar.close - fvg.lower_price),
                    abs(latest_bar.close - fvg.upper_price),
                )
                if dist <= proximity_threshold:
                    matching_fvg = fvg
                    break
        if matching_fvg is None:
            return None

        # --- Rule 6: Liquidity Sweep Check ---
        buyside_swept = any(
            lvl.type == LiquidityType.BUY_SIDE and lvl.is_swept
            for lvl in market_state.smc_state.liquidity_levels
        )
        if not buyside_swept:
            return None

        # --- Rule 7: Displacement Check ---
        latest_idx = len(market_state.bars) - 1
        displacement_confirmed = any(
            d.direction == "BEARISH" and (latest_idx - d.bar_index) <= self.lookback_bars
            for d in market_state.smc_state.displacements
        )
        if not displacement_confirmed:
            return None

        # --- Calculate Zones ---
        entry_zone = (round(matching_ob.low, 5), round(matching_ob.high, 5))
        stop_buffer = self.stop_buffer_pips * self.pip_size
        stop_zone = (round(matching_ob.high, 5), round(matching_ob.high + stop_buffer, 5))

        latest_low = market_state.swing_graph.get_latest_low()
        if latest_low is not None:
            target_price = latest_low.price
        else:
            target_price = matching_ob.low - 2.0 * (stop_zone[1] - matching_ob.low)
        target_zone = (round(target_price - stop_buffer, 5), round(target_price, 5))

        # Generate Setup ID
        timestamp = datetime.now()
        ts_str = timestamp.strftime("%Y%m%d_%H%M%S_%f")
        unique_id = uuid.uuid4().hex[:8]
        setup_id = f"setup_bearish_continuation_{market_state.symbol}_{market_state.timeframe.value}_{unique_id}_{ts_str}"

        return TradeSetup(
            setup_id=setup_id,
            symbol=market_state.symbol,
            timeframe=market_state.timeframe,
            direction=SignalDirection.SELL,
            entry_zone=entry_zone,
            stop_zone=stop_zone,
            target_zone=target_zone,
            confidence_score=market_state.structure_state.confidence,
            confluence=[
                "Bearish Trend",
                "Bearish BOS",
                "Inside Bearish OB",
                "Bearish FVG Proximity",
                "Buy-side Liquidity Swept",
                "Bearish Displacement",
            ],
            trigger_reason=(
                f"Bearish continuation setup at {matching_ob.id} confirmed by "
                f"break {last_break.break_id} and nearby FVG {matching_fvg.id}"
            ),
            invalidations=[
                "Price closes above Order Block high",
                "Price breaches Stop Loss zone",
            ],
            related_structure_break=last_break,
            related_order_block=matching_ob,
            related_fvg=matching_fvg,
            timestamp=timestamp,
        )
