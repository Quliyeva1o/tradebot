"""TrendVolumeConfirmationStrategy: bar-by-bar trend-aligned entries
confirmed by a volume spike above the recent-bar average.

Unlike BullishContinuationStrategy/BearishContinuationStrategy or the
session-scoped strategies (AccumulationBreakoutStrategy,
OpeningRangeBreakoutStrategy), this strategy has no session/time-of-day
restriction and no SMC (order block / FVG / liquidity / displacement)
dependency: it evaluates the trend + volume condition on every bar, all
day, for as long as MarketStructureEngine reports a directional trend.

Logic:
1. Trend: reused directly from MarketStructureEngine's already-tested
   structure_state.trend (BULLISH -> BUY candidates only, BEARISH -> SELL
   candidates only, RANGE/TRANSITION/UNKNOWN -> no entry).
2. Volume confirmation: the trend-aligned closing bar's volume must exceed
   volume_multiplier x the average volume of the last volume_lookback bars.
3. Entry: the confirming bar's close (filled via the existing pending-order
   N+1 mechanism in backtest/engine.py).
4. Stop loss: a percentage buffer beyond the last opposite-direction MAJOR
   swing (structure_state.active_major_low/high).
5. Take profit: a fixed risk_reward multiple of the stop distance -- no
   trailing in this first version.
"""

import uuid
from dataclasses import dataclass

from core.models import SignalDirection
from core.validation import require_positive
from market_structure.structure_models import MarketState, StructureTrend
from strategy.diagnostics import RejectionReason, StrategyDiagnostics
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup


@dataclass(frozen=True)
class TrendVolumeConfirmationConfig:
    """Configuration class for TrendVolumeConfirmationStrategy."""

    volume_lookback: int = 20
    volume_multiplier: float = 1.5
    stop_buffer_pct: float = 0.001
    risk_reward: float = 2.0

    def __post_init__(self) -> None:
        """Validates parameter ranges.

        Raises:
            ValueError: If volume_lookback, volume_multiplier,
                stop_buffer_pct, or risk_reward is not strictly positive.
        """
        require_positive(self.volume_lookback, "volume_lookback")
        require_positive(self.volume_multiplier, "volume_multiplier")
        require_positive(self.stop_buffer_pct, "stop_buffer_pct")
        require_positive(self.risk_reward, "risk_reward")


class TrendVolumeConfirmationStrategy(TradeSetupStrategy):
    """Trend-aligned, volume-confirmed continuation strategy.

    Evaluates every bar for a trend-aligned entry (BUY while structure is
    BULLISH, SELL while BEARISH) confirmed by a volume spike, without
    mutating MarketState.
    """

    def __init__(
        self,
        volume_lookback: int = 20,
        volume_multiplier: float = 1.5,
        stop_buffer_pct: float = 0.001,
        risk_reward: float = 2.0,
        config: TrendVolumeConfirmationConfig | None = None,
    ) -> None:
        """Initializes the TrendVolumeConfirmationStrategy with parameters or config.

        Args:
            volume_lookback: Lookback window (bars) for the volume average.
            volume_multiplier: Volume spike threshold as a multiple of the
                volume_lookback-bar average volume.
            stop_buffer_pct: Stop loss buffer beyond the anchoring major
                swing, as a fraction of that swing's price (e.g. 0.001 = 0.1%).
            risk_reward: Fixed reward:risk multiple applied to the stop
                distance to derive the take profit.
            config: TrendVolumeConfirmationConfig options overlay.

        Raises:
            TypeError: If config is provided but is not a
                TrendVolumeConfirmationConfig.
            ValueError: If any parameter fails
                TrendVolumeConfirmationConfig's validity constraints (see
                its __post_init__).
        """
        if config is not None:
            if not isinstance(config, TrendVolumeConfirmationConfig):
                raise TypeError(
                    f"config must be a TrendVolumeConfirmationConfig, got {type(config).__name__}"
                )
        else:
            config = TrendVolumeConfirmationConfig(
                volume_lookback=volume_lookback,
                volume_multiplier=volume_multiplier,
                stop_buffer_pct=stop_buffer_pct,
                risk_reward=risk_reward,
            )

        self.volume_lookback = config.volume_lookback
        self.volume_multiplier = config.volume_multiplier
        self.stop_buffer_pct = config.stop_buffer_pct
        self.risk_reward = config.risk_reward
        self.diagnostics = StrategyDiagnostics()

    def reset(self) -> None:
        """Resets diagnostics counters (this strategy holds no other state)."""
        self.diagnostics.reset()

    def _reject(self, reason: RejectionReason) -> None:
        """Records a rejection reason and returns None (for use in `return self._reject(...)`)."""
        self.diagnostics.record_rejection(reason)
        return None

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates rules for a trend-aligned, volume-confirmed entry.

        Required checks:
        1. Trend is BULLISH (-> BUY) or BEARISH (-> SELL); RANGE/TRANSITION/
           UNKNOWN is a no-trade condition.
        2. Latest closed bar's volume exceeds volume_multiplier x the
           volume_lookback-bar average volume.
        3. A MAJOR swing opposite the trade direction exists, to anchor the
           stop loss.
        4. Positive risk distance.
        """
        self.diagnostics.record_evaluation()

        # --- Rule 1: Trend Check ---
        trend = market_state.structure_state.trend
        if trend == StructureTrend.BULLISH:
            direction = SignalDirection.BUY
        elif trend == StructureTrend.BEARISH:
            direction = SignalDirection.SELL
        else:
            return self._reject(RejectionReason.NO_TREND)

        latest_bar = market_state.get_latest_bar()
        if latest_bar is None:
            return self._reject(RejectionReason.NO_LATEST_BAR)

        # --- Rule 2: Volume Confirmation Check ---
        recent_bars = market_state.bars_view()
        window = recent_bars[-self.volume_lookback :]
        have_full_window = len(recent_bars) >= self.volume_lookback
        avg_volume = sum(b.volume for b in window) / len(window) if window else 0.0
        volume_ok = have_full_window and latest_bar.volume > avg_volume * self.volume_multiplier
        if not volume_ok:
            return self._reject(RejectionReason.NO_VOLUME_SPIKE)

        # --- Rule 3: Major Swing (SL anchor) Check ---
        if direction == SignalDirection.BUY:
            anchor_swing = market_state.structure_state.active_major_low
        else:
            anchor_swing = market_state.structure_state.active_major_high
        if anchor_swing is None:
            return self._reject(RejectionReason.NO_MAJOR_SWING_FOR_SL)

        # --- Calculate Entry/Stop/Target ---
        entry = latest_bar.close
        buffer = anchor_swing.price * self.stop_buffer_pct
        if direction == SignalDirection.BUY:
            sl = anchor_swing.price - buffer
            risk_dist = entry - sl
        else:
            sl = anchor_swing.price + buffer
            risk_dist = sl - entry

        if risk_dist <= 0.0:
            return self._reject(RejectionReason.NON_POSITIVE_RISK)

        reward_dist = risk_dist * self.risk_reward
        tp = entry + reward_dist if direction == SignalDirection.BUY else entry - reward_dist

        direction_label = "Bullish" if direction == SignalDirection.BUY else "Bearish"

        unique_id = uuid.uuid4().hex[:8]
        ts_str = latest_bar.timestamp.strftime("%Y%m%d_%H%M%S_%f")
        setup_id = (
            f"setup_trend_volume_confirmation_{market_state.symbol}_"
            f"{market_state.timeframe.value}_{direction.name}_{unique_id}_{ts_str}"
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
            confidence_score=market_state.structure_state.confidence,
            confluence=[
                f"{direction_label} trend (MarketStructureEngine)",
                f"Volume {latest_bar.volume:.1f} > {self.volume_multiplier}x "
                f"{self.volume_lookback}-bar avg ({avg_volume:.1f})",
                f"Major {'low' if direction == SignalDirection.BUY else 'high'} SL anchor",
            ],
            trigger_reason=(
                f"{direction_label} trend-volume confirmation at {entry:.5f}, "
                f"volume spike {latest_bar.volume:.1f}/{avg_volume:.1f}"
            ),
            invalidations=[
                "Price breaches Stop Loss zone",
                "Structure trend flips against the position",
            ],
            related_structure_break=None,
            related_order_block=None,
            related_fvg=None,
            timestamp=latest_bar.timestamp,
            strategy_name=self.__class__.__name__,
        )
