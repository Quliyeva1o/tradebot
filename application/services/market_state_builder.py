"""Market State Builder application service.

Orchestrates the incremental processing pipeline to construct and update MarketState.
"""

from core.models import Bar, Timeframe
from market_structure.structure_engine import MarketStructureEngine
from market_structure.structure_models import MarketState, SMCState, StructureState, SwingGraph
from market_structure.swing_detector import SwingDetector
from smc.pipeline import SMCPipeline


class MarketStateBuilder:
    """Orchestrator that updates a single MarketState aggregate root on new bar closes."""

    def __init__(
        self,
        symbol: str,
        timeframe: Timeframe,
        swing_detector: SwingDetector | None = None,
        structure_engine: MarketStructureEngine | None = None,
        smc_pipeline: SMCPipeline | None = None,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.swing_detector = swing_detector or SwingDetector()
        self.structure_engine = structure_engine or MarketStructureEngine()
        self.smc_pipeline = smc_pipeline or SMCPipeline()

        self._market_state = MarketState(
            symbol=self.symbol,
            timeframe=self.timeframe,
        )

    def initialize(self, history: list[Bar]) -> None:
        self._market_state._bars.clear()
        self._market_state.swing_graph = SwingGraph()
        self._market_state.structure_state = StructureState()
        self._market_state.smc_state = SMCState()

        self.structure_engine.reset()

        for bar in history:
            self.append_bar(bar)

    def append_bar(self, bar: Bar) -> MarketState:
        """Appends a new closed bar and updates the market state incrementally."""
        self._market_state.append_bar(bar)

        # Incremental swing detection (Bug #17: bars_view() avoids an O(n)
        # full-history copy on every bar; safe because detect_incremental
        # only ever reads the sequence by index, never mutates it)
        result = self.swing_detector.detect_incremental(
            self._market_state.bars_view(), self._market_state.swing_graph
        )

        # Handle MINOR -> MAJOR swing upgrades first (Bug #1)
        if result.upgraded_swing is not None:
            self.structure_engine.handle_upgrade(result.upgraded_swing)

        new_swing = result.new_swing
        is_replacement = result.is_replacement

        if new_swing:
            if is_replacement:
                # Rebuild structure engine state since a past swing was replaced
                self.structure_engine.reset()
                for s in self._market_state.swing_graph.nodes:
                    self.structure_engine.update(s)
            else:
                self._market_state.swing_graph.add_swing(new_swing)
                self.structure_engine.update(new_swing)

        new_break = self.structure_engine.check_structural_break(bar)

        self._market_state.structure_state = self.structure_engine.get_structure_state()

        self.smc_pipeline.update(self._market_state, bar, new_break)

        return self._market_state

    @property
    def market_state(self) -> MarketState:
        return self._market_state