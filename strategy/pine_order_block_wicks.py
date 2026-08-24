"""PineOrderBlockWicksStrategy: a tradeable strategy built on top of the
'ICT MTF Order Block Wicks [MK]' Pine indicator's own 2-candle wick order
block detection (see smc/pine_order_block.py).

The indicator itself only draws zones and fires alerts -- it has no
entry/exit/SL/TP rules, so this strategy adds trading rules chosen with the
user for backtesting purposes:

1. Entry is at the OB's near edge -- the same price the indicator's own
   "entrychangecolor" logic uses to flag that price has entered the zone:
   bull OB -> zone top (prior candle's high); bear OB -> zone bottom (prior
   candle's low). This is "full zone entry" (touch), not the 20%-incursion
   alert threshold.
2. Stop loss is the OB's opposite edge -- the same price at which the
   indicator's own "Normal"/"Wicks" mitigation deletes the box.
3. Take profit is a fixed 3R (risk_reward=3.0 default, as chosen).
4. Each OB is traded at most once, tracked via `_used_ob_ids`, mirroring
   OrderBlockRetestStrategy's convention (strategy/order_block_retest.py).
5. If multiple untouched, unused OBs are touched on the same bar, the most
   recently formed one wins (same recency tiebreak as
   OrderBlockRetestStrategy).
6. Optional min_risk_distance filter: backtest analysis (2022-04..2026-07,
   XAUUSD M15) showed OB zones smaller than roughly a few times the typical
   per-bar spread are dominated by spread noise rather than genuine
   structure -- their average R was -0.71 (bottom risk-distance quintile)
   versus roughly breakeven-to-slightly-positive for the largest quintiles.
   Rejecting sub-threshold zones is a data-driven filter, not part of the
   original Pine indicator.
"""

import uuid

from core.models import SignalDirection
from market_structure.structure_models import MarketState
from smc.pine_order_block import PineOBDirection, PineOrderBlock, PineOrderBlockTracker
from strategy.diagnostics import RejectionReason, StrategyDiagnostics
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup


class PineOrderBlockWicksStrategy(TradeSetupStrategy):
    """Trades zone-touch entries against the Pine indicator's own OB detection."""

    def __init__(
        self,
        risk_reward: float = 3.0,
        max_active_obs: int = 8,
        min_risk_distance: float = 0.0,
    ) -> None:
        """Initializes the strategy.

        Args:
            risk_reward: Fixed reward:risk multiple applied to the stop distance.
            max_active_obs: Max concurrent zones per direction (matches the
                indicator's "MAX OBs" setting for the traded timeframe).
            min_risk_distance: Minimum OB zone width (top-bottom, in price
                units) required to trade it. 0.0 (default) disables the
                filter, matching the original indicator's behavior.
        """
        self.risk_reward = risk_reward
        self.min_risk_distance = min_risk_distance
        self.tracker = PineOrderBlockTracker(max_active=max_active_obs)
        self.diagnostics = StrategyDiagnostics()
        self._used_ob_ids: set[str] = set()

    def reset(self) -> None:
        """Resets diagnostics, the OB tracker, and the once-per-OB usage memory."""
        self.diagnostics.reset()
        self.tracker.reset()
        self._used_ob_ids.clear()

    def _reject(self, reason: RejectionReason) -> None:
        """Records a rejection reason and returns None (for use in `return self._reject(...)`)."""
        self.diagnostics.record_rejection(reason)
        return None

    def _is_touched(self, ob: PineOrderBlock, bar_low: float, bar_high: float) -> bool:
        """Whether the current bar's wicks cross the OB's near (entry) edge."""
        if ob.direction == PineOBDirection.BULLISH:
            return bar_low < ob.top
        return bar_high > ob.bottom

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates rules for the Pine order block wick-touch setup.

        Required checks:
        1. At least two bars exist (so the tracker can run)
        2. At least one active OB has not already been traded by this strategy
        3. The current bar touches an untraded OB's near edge
        4. Positive risk distance
        """
        self.diagnostics.record_evaluation()

        bars = market_state.bars_view()
        if len(bars) < 2:
            return self._reject(RejectionReason.NO_LATEST_BAR)

        latest_bar = bars[-1]
        self.tracker.update(bars)

        candidates = [
            ob
            for ob in (*self.tracker.bull_obs, *self.tracker.bear_obs)
            if ob.id not in self._used_ob_ids
        ]
        if not candidates:
            return self._reject(RejectionReason.NO_ORDER_BLOCKS)

        touched = [
            ob for ob in candidates if self._is_touched(ob, latest_bar.low, latest_bar.high)
        ]
        if not touched:
            return self._reject(RejectionReason.NO_TOUCH_DETECTED)

        matching_ob = max(touched, key=lambda ob: ob.bar_index)

        if matching_ob.direction == PineOBDirection.BULLISH:
            direction = SignalDirection.BUY
            entry = matching_ob.top
            sl = matching_ob.bottom
            risk_dist = entry - sl
        else:
            direction = SignalDirection.SELL
            entry = matching_ob.bottom
            sl = matching_ob.top
            risk_dist = sl - entry

        if risk_dist <= 0.0:
            return self._reject(RejectionReason.NON_POSITIVE_RISK)
        if risk_dist < self.min_risk_distance:
            return self._reject(RejectionReason.ZONE_TOO_SMALL)

        reward_dist = risk_dist * self.risk_reward
        tp = entry + reward_dist if direction == SignalDirection.BUY else entry - reward_dist

        self._used_ob_ids.add(matching_ob.id)

        direction_label = "Bullish" if direction == SignalDirection.BUY else "Bearish"

        unique_id = uuid.uuid4().hex[:8]
        timestamp = latest_bar.timestamp
        ts_str = timestamp.strftime("%Y%m%d_%H%M%S_%f")
        setup_id = (
            f"setup_pine_ob_{market_state.symbol}_{market_state.timeframe.value}_"
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
                f"{direction_label} Pine 2-candle wick Order Block ({matching_ob.id})",
                f"Fixed {self.risk_reward:.1f}R target",
            ],
            trigger_reason=(
                f"{direction_label} Order Block {matching_ob.id} touched at {entry:.5f}"
            ),
            invalidations=[
                "Price closes through the Order Block's far (mitigation) edge",
                "Price breaches Stop Loss zone",
            ],
            related_structure_break=None,
            related_order_block=None,
            related_fvg=None,
            timestamp=timestamp,
            strategy_name=self.__class__.__name__,
        )
