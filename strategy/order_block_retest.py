"""OrderBlockRetestStrategy: the user's 4th strategy (plain-text spec).

Reuses the existing SMC pipeline's Order Blocks (market_state.smc_state.
order_blocks, produced by OrderBlockDetector from the structure-break
history) -- this strategy does NOT detect order blocks itself, it only
reacts to ones the pipeline has already computed.

Logic:
1. A bullish (support) Order Block is "touched" when price descends into
   its upper edge: bar.low <= ob.high <= bar.high.
2. A bearish (resistance) Order Block is "touched" when price rises into
   its lower edge: bar.low <= ob.low <= bar.high.
3. Entry is the OB edge level itself (ob.high for bullish, ob.low for
   bearish) -- not the bar's close -- filled via the existing pending-limit-
   order mechanics in BacktestEngine, same as the OB-zone entries in
   continuation.py.
4. Stop loss is the OB's opposite edge (ob.low for bullish, ob.high for
   bearish); take profit is a fixed 2R (configurable via risk_reward).
5. Each Order Block (by its own id) is traded AT MOST ONCE, tracked via
   `self._used_ob_ids` -- a strategy-local "already traded this one" memory,
   deliberately independent of OrderBlock.is_mitigated. Mitigation (Bug #22)
   means "price closed fully through the opposite edge, invalidating the
   zone structurally" -- a different concept from "this strategy has
   already acted on it." An OB could in principle be touched-and-traded
   many bars before ever becoming mitigated (or never becoming mitigated at
   all), so relying on is_mitigated as the "used" guard would be both wrong
   (it doesn't track *this strategy's* usage) and unreliable (mitigation
   might never happen for an OB this strategy already traded). This
   strategy does not filter on is_mitigated at all -- only on
   `_used_ob_ids` -- per the spec's explicit instruction to keep this
   guard independent of mitigation semantics.
6. No session restriction: active at any time, any day.
7. If multiple untouched, unused order blocks are touched on the same bar,
   the most recently formed one (highest bar_index) wins, mirroring the
   recency tiebreak used for OB/FVG selection in continuation.py (Bug #10).
"""

import uuid
from dataclasses import dataclass

from core.models import SignalDirection
from core.validation import require_positive
from market_structure.structure_models import MarketState
from smc.order_block import OBDirection, OrderBlock
from strategy.diagnostics import RejectionReason, StrategyDiagnostics
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup


@dataclass(frozen=True)
class OrderBlockRetestConfig:
    """Configuration class for OrderBlockRetestStrategy."""

    risk_reward: float = 2.0

    def __post_init__(self) -> None:
        """Validates parameter ranges.

        Raises:
            ValueError: If risk_reward is not strictly positive.
        """
        require_positive(self.risk_reward, "risk_reward")


class OrderBlockRetestStrategy(TradeSetupStrategy):
    """Ports the user's Order Block Retest strategy description.

    Evaluates whether the current bar touches an as-yet-unused Order Block
    edge from the existing SMC pipeline, without mutating MarketState.
    """

    def __init__(
        self,
        risk_reward: float = 2.0,
        config: OrderBlockRetestConfig | None = None,
    ) -> None:
        """Initializes the OrderBlockRetestStrategy with parameters or config.

        Args:
            risk_reward: Fixed reward:risk multiple applied to the stop distance.
            config: OrderBlockRetestConfig options overlay.

        Raises:
            TypeError: If config is provided but is not an
                OrderBlockRetestConfig.
            ValueError: If risk_reward is not strictly positive.
        """
        if config is not None:
            if not isinstance(config, OrderBlockRetestConfig):
                raise TypeError(
                    f"config must be an OrderBlockRetestConfig, got {type(config).__name__}"
                )
        else:
            config = OrderBlockRetestConfig(risk_reward=risk_reward)

        self.risk_reward = config.risk_reward

        self.diagnostics = StrategyDiagnostics()
        self._used_ob_ids: set[str] = set()

    def reset(self) -> None:
        """Resets diagnostics and the once-per-OB usage memory (fresh backtest run)."""
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
        """Evaluates rules for the Order Block retest setup.

        Required checks:
        1. At least one Order Block exists in the SMC pipeline's state
        2. At least one of those has not already been traded by this strategy
        3. The current bar touches an untraded Order Block's edge
        4. Positive risk distance and a valid risk_reward configuration
        """
        self.diagnostics.record_evaluation()

        latest_bar = market_state.get_latest_bar()
        if latest_bar is None:
            return self._reject(RejectionReason.NO_LATEST_BAR)

        order_blocks = market_state.smc_state.order_blocks
        if not order_blocks:
            return self._reject(RejectionReason.NO_ORDER_BLOCKS)

        candidates = [ob for ob in order_blocks if ob.id not in self._used_ob_ids]
        if not candidates:
            return self._reject(RejectionReason.OB_ALREADY_USED)

        touched = [
            ob for ob in candidates if self._is_touched(ob, latest_bar.low, latest_bar.high)
        ]
        if not touched:
            return self._reject(RejectionReason.NO_TOUCH_DETECTED)

        matching_ob = max(touched, key=lambda ob: ob.bar_index)

        if matching_ob.direction == OBDirection.BULLISH:
            direction = SignalDirection.BUY
            entry = matching_ob.high
            sl = matching_ob.low
            risk_dist = entry - sl
        else:
            direction = SignalDirection.SELL
            entry = matching_ob.low
            sl = matching_ob.high
            risk_dist = sl - entry

        if risk_dist <= 0.0:
            return self._reject(RejectionReason.NON_POSITIVE_RISK)
        if self.risk_reward <= 0.0:
            return self._reject(RejectionReason.RR_GATE_FAILED)

        reward_dist = risk_dist * self.risk_reward
        tp = entry + reward_dist if direction == SignalDirection.BUY else entry - reward_dist

        self._used_ob_ids.add(matching_ob.id)

        direction_label = "Bullish" if direction == SignalDirection.BUY else "Bearish"

        unique_id = uuid.uuid4().hex[:8]
        timestamp = latest_bar.timestamp
        ts_str = timestamp.strftime("%Y%m%d_%H%M%S_%f")
        setup_id = (
            f"setup_ob_retest_{market_state.symbol}_{market_state.timeframe.value}_"
            f"{direction.name}_{unique_id}_{ts_str}"
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
                f"{direction_label} Order Block retest ({matching_ob.id})",
                f"Fixed {self.risk_reward:.1f}R target",
            ],
            trigger_reason=(
                f"{direction_label} Order Block {matching_ob.id} retested at "
                f"{entry:.5f} (edge touch)"
            ),
            invalidations=[
                "Price closes through the Order Block's far edge",
                "Price breaches Stop Loss zone",
            ],
            related_structure_break=None,
            related_order_block=matching_ob,
            related_fvg=None,
            timestamp=timestamp,
            strategy_name=self.__class__.__name__,
        )
