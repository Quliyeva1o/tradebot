"""SimpleLiquiditySweepStrategy: a bare, 2-candle liquidity-grab pattern
sourced from a discretionary trader, ported here for objective testing.

Unlike every other strategy in this codebase, this one has no session/
time-of-day restriction, no trend filter, no SMC (order block / FVG /
liquidity / displacement) dependency, and no one-trade-per-day guard: it
evaluates the raw 2-bar pattern on every bar, all day, and can generate
any number of setups per day (the only limit on concurrent exposure is the
existing engine-level single-open-position constraint, enforced by
backtest/engine.py, not by this strategy).

Logic:
1. On every bar, compare the latest (just-closed) bar against the
   previous one:
   - BULLISH sweep: latest.low < prev.low AND latest.close > prev.low
     (price dipped below the prior bar's low, then reclaimed it by close).
   - BEARISH sweep: latest.high > prev.high AND latest.close < prev.high
     (mirror: price spiked above the prior bar's high, then reclaimed it).
   An outside bar can satisfy both conditions at once (it sweeps both
   sides and closes back inside the prior bar's range); when that
   happens, the bar's own body direction (close vs. open) breaks the tie,
   since that is the only signal in the bar itself indicating which side
   the reclaim actually favored. A doji (close == open) has no such
   signal and is treated as no setup.
2. Entry: the sweep bar's close (filled via the existing pending-order N+1
   mechanism in backtest/engine.py).
3. Stop loss: a percentage buffer beyond the sweep bar's far wick (the low
   for a bullish sweep, the high for a bearish one).
4. Take profit: entry +/- tp_wick_multiplier x the sweep bar's wick length
   (prev.low - sweep.low for bullish, sweep.high - prev.high for
   bearish) -- not a risk_reward multiple of the stop distance, per the
   original trader's rule.
"""

import uuid

from dataclasses import dataclass

from core.models import SignalDirection
from core.validation import require_positive
from market_structure.structure_models import MarketState
from strategy.diagnostics import RejectionReason, StrategyDiagnostics
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup


@dataclass(frozen=True)
class SimpleLiquiditySweepConfig:
    """Configuration class for SimpleLiquiditySweepStrategy."""

    stop_buffer_pct: float = 0.0005
    tp_wick_multiplier: float = 2.0

    def __post_init__(self) -> None:
        """Validates parameter ranges.

        Raises:
            ValueError: If stop_buffer_pct or tp_wick_multiplier is not
                strictly positive.
        """
        require_positive(self.stop_buffer_pct, "stop_buffer_pct")
        require_positive(self.tp_wick_multiplier, "tp_wick_multiplier")


class SimpleLiquiditySweepStrategy(TradeSetupStrategy):
    """Bare 2-candle liquidity sweep + reclaim, no session/trend filter.

    Evaluates whether the latest bar swept and reclaimed the previous
    bar's high or low, without mutating MarketState.
    """

    def __init__(
        self,
        stop_buffer_pct: float = 0.0005,
        tp_wick_multiplier: float = 2.0,
        config: SimpleLiquiditySweepConfig | None = None,
    ) -> None:
        """Initializes the SimpleLiquiditySweepStrategy with parameters or config.

        Args:
            stop_buffer_pct: Stop loss buffer beyond the sweep bar's far
                wick, as a fraction of that wick's price (e.g. 0.0005 = 0.05%).
            tp_wick_multiplier: Take profit distance as a multiple of the
                sweep bar's wick length (prev.low/high to sweep.low/high).
            config: SimpleLiquiditySweepConfig options overlay.

        Raises:
            TypeError: If config is provided but is not a
                SimpleLiquiditySweepConfig.
            ValueError: If any parameter fails SimpleLiquiditySweepConfig's
                validity constraints (see its __post_init__).
        """
        if config is not None:
            if not isinstance(config, SimpleLiquiditySweepConfig):
                raise TypeError(
                    f"config must be a SimpleLiquiditySweepConfig, got {type(config).__name__}"
                )
        else:
            config = SimpleLiquiditySweepConfig(
                stop_buffer_pct=stop_buffer_pct,
                tp_wick_multiplier=tp_wick_multiplier,
            )

        self.stop_buffer_pct = config.stop_buffer_pct
        self.tp_wick_multiplier = config.tp_wick_multiplier
        self.diagnostics = StrategyDiagnostics()

    def reset(self) -> None:
        """Resets diagnostics counters (this strategy holds no other state)."""
        self.diagnostics.reset()

    def _reject(self, reason: RejectionReason) -> None:
        """Records a rejection reason and returns None (for use in `return self._reject(...)`)."""
        self.diagnostics.record_rejection(reason)
        return None

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates rules for the 2-candle liquidity sweep + reclaim setup.

        Required checks:
        1. At least two bars exist (latest + previous)
        2. Latest bar sweeps and reclaims the previous bar's low or high
        3. Positive risk distance
        """
        self.diagnostics.record_evaluation()

        latest_bar = market_state.get_latest_bar()
        if latest_bar is None:
            return self._reject(RejectionReason.NO_LATEST_BAR)

        bars = market_state.bars_view()
        if len(bars) < 2:
            return self._reject(RejectionReason.NO_PREV_BAR)
        prev_bar = bars[-2]

        bullish_sweep = latest_bar.low < prev_bar.low and latest_bar.close > prev_bar.low
        bearish_sweep = latest_bar.high > prev_bar.high and latest_bar.close < prev_bar.high

        if bullish_sweep and bearish_sweep:
            # Outside bar sweeping both sides at once: break the tie with the
            # bar's own body direction (the only signal within the bar itself
            # indicating which side the reclaim favored). A doji has none.
            if latest_bar.close > latest_bar.open:
                bearish_sweep = False
            elif latest_bar.close < latest_bar.open:
                bullish_sweep = False
            else:
                bullish_sweep = bearish_sweep = False

        if not bullish_sweep and not bearish_sweep:
            return self._reject(RejectionReason.NO_SWEEP)

        direction = SignalDirection.BUY if bullish_sweep else SignalDirection.SELL

        entry = latest_bar.close
        if direction == SignalDirection.BUY:
            wick_length = prev_bar.low - latest_bar.low
            sl = latest_bar.low * (1.0 - self.stop_buffer_pct)
            risk_dist = entry - sl
        else:
            wick_length = latest_bar.high - prev_bar.high
            sl = latest_bar.high * (1.0 + self.stop_buffer_pct)
            risk_dist = sl - entry

        if risk_dist <= 0.0:
            return self._reject(RejectionReason.NON_POSITIVE_RISK)
        if self.tp_wick_multiplier <= 0.0:
            return self._reject(RejectionReason.RR_GATE_FAILED)

        reward_dist = wick_length * self.tp_wick_multiplier
        tp = entry + reward_dist if direction == SignalDirection.BUY else entry - reward_dist

        direction_label = "Bullish" if direction == SignalDirection.BUY else "Bearish"
        swept_level = "low" if direction == SignalDirection.BUY else "high"
        swept_price = prev_bar.low if direction == SignalDirection.BUY else prev_bar.high

        unique_id = uuid.uuid4().hex[:8]
        ts_str = latest_bar.timestamp.strftime("%Y%m%d_%H%M%S_%f")
        setup_id = (
            f"setup_simple_liquidity_sweep_{market_state.symbol}_{market_state.timeframe.value}_"
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
                f"{direction_label} 2-candle liquidity sweep+reclaim of prior bar's {swept_level}",
                f"Wick length {wick_length:.5f}, TP at {self.tp_wick_multiplier}x wick",
            ],
            trigger_reason=(
                f"{direction_label} sweep: prior bar's {swept_level} ({swept_price:.5f}) "
                f"swept and reclaimed at {entry:.5f}"
            ),
            invalidations=[
                "Price breaches Stop Loss zone",
            ],
            related_structure_break=None,
            related_order_block=None,
            related_fvg=None,
            timestamp=latest_bar.timestamp,
            strategy_name=self.__class__.__name__,
        )
