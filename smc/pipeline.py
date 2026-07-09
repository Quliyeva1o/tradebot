"""Smart Money Concepts (SMC) analysis pipeline service.

Coordinates FVG, Order Blocks, Liquidity Pools, Displacement, and Mitigation monitoring.
"""

from core.models import Bar
from market_structure.structure_models import MarketState, StructureBreak, StructureState
from market_structure.swing_models import SwingClassification, SwingType
from smc.displacement import DisplacementBar, DisplacementDetector
from smc.fvg import FairValueGap, FVGDetector
from smc.liquidity import LiquidityDetector
from smc.mitigation import MitigationMonitor
from smc.order_block import OrderBlockDetector
from smc.premium_discount import PremiumDiscountCalculator


class SMCPipeline:
    """Coordinates the execution of all Smart Money Concepts detectors in a unified pipeline."""

    def __init__(
        self,
        fvg_detector: FVGDetector | None = None,
        ob_detector: OrderBlockDetector | None = None,
        liquidity_detector: LiquidityDetector | None = None,
        displacement_detector: DisplacementDetector | None = None,
        mitigation_monitor: MitigationMonitor | None = None,
        premium_discount_calculator: PremiumDiscountCalculator | None = None,
        max_zone_age_bars: int | None = None,
    ) -> None:
        """Initializes the SMCPipeline with specific detector instances.

        Args:
            fvg_detector: Fair Value Gap detector.
            ob_detector: Order Block detector.
            liquidity_detector: Liquidity Pools detector.
            displacement_detector: Displacement run detector.
            mitigation_monitor: Mitigation status monitor.
            premium_discount_calculator: Premium/Discount zone calculator.
            max_zone_age_bars: Maximum age (in bars) to keep mitigated zones.
        """
        self.fvg_detector = fvg_detector or FVGDetector()
        self.ob_detector = ob_detector or OrderBlockDetector()
        self.liquidity_detector = liquidity_detector or LiquidityDetector()
        self.displacement_detector = displacement_detector or DisplacementDetector()
        self.mitigation_monitor = mitigation_monitor or MitigationMonitor()
        self.premium_discount_calculator = (
            premium_discount_calculator or PremiumDiscountCalculator()
        )
        self.max_zone_age_bars = max_zone_age_bars

    def update(
        self,
        market_state: MarketState,
        new_bar: Bar,
        new_break: StructureBreak | None,
    ) -> None:
        """Executes all SMC detectors and updates the MarketState SMCState references.

        Args:
            market_state: The current active MarketState domain aggregate.
            new_bar: The newly closed candle bar.
            new_break: The structure break detected on the newly closed candle (BOS/CHoCH).
        """
        bars = market_state.bars

        # 1. FVG Detection (checks the latest 3-bar sequence)
        if len(bars) >= 3:
            new_fvgs = self.fvg_detector.detect_fvgs(bars[-3:])
            for fvg in new_fvgs:
                offset = len(bars) - 3
                adjusted_fvg = FairValueGap(
                    id=f"fvg_{fvg.start_index + offset}_{fvg.direction.value.lower()}",
                    start_index=fvg.start_index + offset,
                    end_index=fvg.end_index + offset,
                    upper_price=fvg.upper_price,
                    lower_price=fvg.lower_price,
                    direction=fvg.direction,
                    timestamp=fvg.timestamp,
                    is_mitigated=fvg.is_mitigated,
                )
                market_state.smc_state.fair_value_gaps.append(adjusted_fvg)

        # 2. Order Block Detection (runs on structural breaks)
        if new_break:
            temp_state = StructureState(
                trend=market_state.structure_state.trend,
                confidence=market_state.structure_state.confidence,
                active_major_high=market_state.structure_state.active_major_high,
                active_major_low=market_state.structure_state.active_major_low,
                breaks_history=[new_break],
            )
            new_obs = self.ob_detector.detect_order_blocks(bars, temp_state)
            market_state.smc_state.order_blocks.extend(new_obs)

        # 3. Liquidity Pools Detection (refresh liquidity levels using swing graph)
        market_state.smc_state.liquidity_levels = self.liquidity_detector.update_incremental(
            market_state.swing_graph
        )

        # 4. Displacement Run Detection (runs on a constant maximum window size for ATR O(1))
        window_size = self.displacement_detector.atr_period + 100
        if len(bars) >= window_size:
            sub_bars = bars[-window_size:]
            disps = self.displacement_detector.find_displacements(sub_bars)
            offset = len(bars) - window_size
        else:
            sub_bars = bars
            disps = self.displacement_detector.find_displacements(sub_bars)
            offset = 0

        for disp in disps:
            if disp.bar_index == len(sub_bars) - 1:
                absolute_index = disp.bar_index + offset
                adjusted_disp = DisplacementBar(
                    bar_index=absolute_index,
                    timestamp=disp.timestamp,
                    direction=disp.direction,
                    range=disp.range,
                    atr=disp.atr,
                )
                market_state.smc_state.displacements.append(adjusted_disp)

        # 5. Mitigation Monitoring (checks mitigation status of active zones)
        market_state.smc_state.fair_value_gaps = self.mitigation_monitor.check_mitigation(
            bars, market_state.smc_state.fair_value_gaps
        )
        market_state.smc_state.order_blocks = self.mitigation_monitor.check_mitigation(
            bars, market_state.smc_state.order_blocks
        )

        # 5b. Mitigated Zone Pruning
        if self.max_zone_age_bars is not None:
            current_idx = len(bars) - 1
            # Prune order blocks
            market_state.smc_state.order_blocks = [
                ob for ob in market_state.smc_state.order_blocks
                if not (ob.is_mitigated and (current_idx - ob.bar_index) > self.max_zone_age_bars)
            ]
            # Prune fair value gaps
            market_state.smc_state.fair_value_gaps = [
                fvg for fvg in market_state.smc_state.fair_value_gaps
                if not (fvg.is_mitigated and (current_idx - fvg.end_index) > self.max_zone_age_bars)
            ]

        # 6. Premium / Discount Zone Calculation
        major_high = market_state.structure_state.active_major_high
        major_low = market_state.structure_state.active_major_low

        if major_high is None:
            for swing in reversed(market_state.swing_graph.nodes):
                if (
                    swing.type == SwingType.HIGH
                    and swing.classification == SwingClassification.MAJOR
                ):
                    major_high = swing
                    break

        if major_low is None:
            for swing in reversed(market_state.swing_graph.nodes):
                if (
                    swing.type == SwingType.LOW
                    and swing.classification == SwingClassification.MAJOR
                ):
                    major_low = swing
                    break

        if major_high is not None and major_low is not None:
            try:
                market_state.premium_discount_zone = self.premium_discount_calculator.calculate(
                    high=major_high.price,
                    low=major_low.price,
                    current_price=new_bar.close,
                )
            except ValueError:
                market_state.premium_discount_zone = None
        else:
            market_state.premium_discount_zone = None
