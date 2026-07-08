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
        """Initializes the MarketStateBuilder.

        Args:
            symbol: Trading instrument symbol.
            timeframe: Candle timeframe interval.
            swing_detector: Injectable SwingDetector instance.
            structure_engine: Injectable MarketStructureEngine instance.
            smc_pipeline: Injectable SMCPipeline instance.
        """
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
        """Initializes the MarketState with a historical sequence of bars.

        Args:
            history: The list of historical candlestick bars.
        """
        self._market_state._bars.clear()
        self._market_state.swing_graph = SwingGraph()
        self._market_state.structure_state = StructureState()
        self._market_state.smc_state = SMCState()

        self.structure_engine.reset()

        for bar in history:
            self.append_bar(bar)

    def append_bar(self, bar: Bar) -> MarketState:
        """Appends a new closed bar and updates the market state incrementally.

        Args:
            bar: The newly closed candle bar.

        Returns:
            The updated MarketState instance.
        """
        # 1. Append the bar
        self._market_state.append_bar(bar)

        # 2. Incremental swing detection
        result = self.swing_detector.detect_incremental(
            self._market_state.bars, self._market_state.swing_graph
        )

        # 3. Handle upgraded swings first
        if result.upgraded_swing:
            self.structure_engine.handle_upgrade(result.upgraded_swing)

        # 4. Handle new swing
        if result.new_swing:
            self._market_state.swing_graph.add_swing(result.new_swing)
            self.structure_engine.update(result.new_swing)

        # 5. Check for structural break on the new bar
        new_break = self.structure_engine.check_structural_break(bar)

        # 6. Update structural state reference
        self._market_state.structure_state = self.structure_engine.get_structure_state()

        # 7. Run the SMC Pipeline
        self.smc_pipeline.update(self._market_state, bar, new_break)

        # 8. Return updated MarketState
        return self._market_state

    @property
    def market_state(self) -> MarketState:
        """Exposes the active MarketState domain aggregate root.

        Returns:
            The current MarketState instance.
        """
        return self._market_state
