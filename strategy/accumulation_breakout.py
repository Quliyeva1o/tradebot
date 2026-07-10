"""AccumulationBreakoutStrategy: port of the user's "5 Candle Accumulation
Breakout Retest" TradingView Pine Script strategy.

Original Pine logic (session-scoped state machine):
1. During a scan session, accumulate a run of up to N consecutive candles
   whose close stays within the *previous* candle's high/low (a tight range).
   A candle breaking that containment restarts the run from itself.
2. Once N candles have accumulated, the range [low, high] is "ready".
3. A breakout fires when a candle closes outside the ready range with an
   above-average body (>1.3x SMA-20 body) and, optionally, an above-average
   volume spike (>volume_multiplier x SMA-20 volume). The breakout flag is
   sticky (persists across bars) once set.
4. A retest fires on any subsequent bar (not gated by session) where price
   dips back to touch the broken range edge but still closes beyond it.
5. Only one trade is taken per session; the range/breakout/trade state resets
   at the start of the next session.

Unlike BullishContinuationStrategy/BearishContinuationStrategy, this
strategy is inherently STATEFUL across evaluate() calls (mirrors the Pine
script's `var` session-scoped variables), since the accumulation range and
breakout flags must persist across bars between evaluations.

Timezone note: Bar.timestamp is confirmed UTC in this codebase (see
config/settings.py Settings.TIMEZONE, default "UTC", and CSVDataProvider
localizes/converts to it). The original Pine script's session string
("0930-1100") is evaluated against the *chart's* session timezone, which for
an FX pair on TradingView is typically the exchange/instrument timezone
(often America/New_York), NOT necessarily UTC. session_start/session_end
here are compared directly against Bar.timestamp.time(), i.e. as literal UTC
clock time -- if the intent was New York session hours, the caller must pass
the UTC-equivalent times (e.g. 13:30-15:00 UTC during EDT, 14:30-16:00 UTC
during EST) instead of the defaults.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, time

from core.models import Bar, SignalDirection
from market_structure.structure_models import MarketState
from strategy.diagnostics import RejectionReason, StrategyDiagnostics
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup


@dataclass(frozen=True)
class AccumulationBreakoutConfig:
    """Configuration class for AccumulationBreakoutStrategy."""

    risk_reward: float = 2.0
    volume_multiplier: float = 1.5
    require_volume_filter: bool = True
    session_start: time = time(9, 30)
    session_end: time = time(11, 0)
    body_multiplier: float = 1.3
    accumulation_bars: int = 5
    sma_period: int = 20


class AccumulationBreakoutStrategy(TradeSetupStrategy):
    """Ports the "5 Candle Accumulation Breakout Retest" Pine Script strategy.

    Evaluates whether the running session-scoped state (accumulation range,
    breakout flag, trade-taken guard) plus the current bar satisfy a valid
    breakout-retest setup, without mutating MarketState.
    """

    def __init__(
        self,
        risk_reward: float = 2.0,
        volume_multiplier: float = 1.5,
        require_volume_filter: bool = True,
        session_start: time = time(9, 30),
        session_end: time = time(11, 0),
        body_multiplier: float = 1.3,
        accumulation_bars: int = 5,
        sma_period: int = 20,
        config: AccumulationBreakoutConfig | None = None,
    ) -> None:
        """Initializes the AccumulationBreakoutStrategy with parameters or config.

        Args:
            risk_reward: Fixed reward:risk multiple applied to the stop distance.
            volume_multiplier: Volume spike threshold as a multiple of SMA-20 volume.
            require_volume_filter: Whether the volume-spike gate is enforced.
                Disable for Forex data where tick-volume does not represent
                real traded volume.
            session_start: UTC time-of-day the scan session opens (inclusive).
            session_end: UTC time-of-day the scan session closes (exclusive).
            body_multiplier: Breakout candle body threshold as a multiple of SMA-20 body.
            accumulation_bars: Number of contained candles required to arm the range.
            sma_period: Lookback window for the body/volume moving averages.
            config: AccumulationBreakoutConfig options overlay.
        """
        if config is not None:
            self.risk_reward = config.risk_reward
            self.volume_multiplier = config.volume_multiplier
            self.require_volume_filter = config.require_volume_filter
            self.session_start = config.session_start
            self.session_end = config.session_end
            self.body_multiplier = config.body_multiplier
            self.accumulation_bars = config.accumulation_bars
            self.sma_period = config.sma_period
        else:
            self.risk_reward = risk_reward
            self.volume_multiplier = volume_multiplier
            self.require_volume_filter = require_volume_filter
            self.session_start = session_start
            self.session_end = session_end
            self.body_multiplier = body_multiplier
            self.accumulation_bars = accumulation_bars
            self.sma_period = sma_period

        self.diagnostics = StrategyDiagnostics()
        self._reset_session_state()
        self._was_in_session = False

    def _reset_session_state(self) -> None:
        """Resets the Pine `var` session-scoped state (mirrors `if newSession`)."""
        self._count = 0
        self._range_high: float | None = None
        self._range_low: float | None = None
        self._range_ready = False
        self._breakout_up = False
        self._breakout_down = False
        self._trade_taken = False

    def reset(self) -> None:
        """Resets diagnostics and all session-scoped state (fresh backtest run)."""
        self.diagnostics.reset()
        self._reset_session_state()
        self._was_in_session = False

    def _reject(self, reason: RejectionReason) -> None:
        """Records a rejection reason and returns None (for use in `return self._reject(...)`)."""
        self.diagnostics.record_rejection(reason)
        return None

    def _in_session(self, timestamp: datetime) -> bool:
        """Whether the given (UTC) timestamp falls in [session_start, session_end)."""
        t = timestamp.time()
        return self.session_start <= t < self.session_end

    def _update_accumulation(self, bar: Bar, prev_bar: Bar | None) -> None:
        """Extends or restarts the accumulation range for one in-session bar.

        Mirrors Pine's `validCandle = close <= high[1] and close >= low[1]`:
        a candle continues the run only if its close stays within the
        immediately preceding candle's high/low; otherwise the run restarts
        from this candle.
        """
        if self._count == 0:
            self._range_high = bar.high
            self._range_low = bar.low
            self._count = 1
        else:
            valid_candle = (
                prev_bar is not None and bar.close <= prev_bar.high and bar.close >= prev_bar.low
            )
            if valid_candle:
                self._range_high = max(self._range_high, bar.high)
                self._range_low = min(self._range_low, bar.low)
                self._count += 1
            else:
                self._range_high = bar.high
                self._range_low = bar.low
                self._count = 1

        if self._count == self.accumulation_bars:
            self._range_ready = True

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates rules for the 5-candle accumulation breakout retest.

        Required checks:
        1. Session-scoped accumulation range is ready (N contained candles)
        2. No trade already taken this session
        3. Breakout: close beyond range with strong body (+ optional volume spike)
        4. Retest: a later bar dips back to the broken edge but still closes beyond it
        5. Positive risk distance and a valid risk_reward configuration
        """
        self.diagnostics.record_evaluation()

        latest_bar = market_state.get_latest_bar()
        if latest_bar is None:
            return self._reject(RejectionReason.NO_LATEST_BAR)

        in_session = self._in_session(latest_bar.timestamp)
        new_session = in_session and not self._was_in_session
        if new_session:
            self._reset_session_state()
        self._was_in_session = in_session

        recent_bars = market_state.bars_view()
        prev_bar = recent_bars[-2] if len(recent_bars) >= 2 else None

        # --- Rule 1: Accumulation Range Check ---
        if not self._range_ready and in_session:
            self._update_accumulation(latest_bar, prev_bar)

        if not self._range_ready:
            if in_session:
                return self._reject(RejectionReason.ACCUMULATION_NOT_READY)
            return self._reject(RejectionReason.NOT_IN_SESSION)

        # --- Rule 2: One Trade Per Session Guard ---
        if self._trade_taken:
            return self._reject(RejectionReason.TRADE_ALREADY_TAKEN)

        # --- Rule 3: Breakout Check ---
        window = recent_bars[-self.sma_period :]
        have_full_window = len(recent_bars) >= self.sma_period
        body = abs(latest_bar.close - latest_bar.open)
        avg_body = sum(abs(b.close - b.open) for b in window) / len(window) if window else 0.0
        avg_volume = sum(b.volume for b in window) / len(window) if window else 0.0

        body_ok = have_full_window and body > avg_body * self.body_multiplier
        volume_ok = (
            True
            if not self.require_volume_filter
            else have_full_window and latest_bar.volume > avg_volume * self.volume_multiplier
        )

        beyond_range = latest_bar.close > self._range_high or latest_bar.close < self._range_low

        if body_ok and volume_ok and latest_bar.close > self._range_high:
            self._breakout_up = True
        if body_ok and volume_ok and latest_bar.close < self._range_low:
            self._breakout_down = True

        if not self._breakout_up and not self._breakout_down:
            if self.require_volume_filter and body_ok and beyond_range and not volume_ok:
                return self._reject(RejectionReason.NO_VOLUME_SPIKE)
            return self._reject(RejectionReason.NO_BREAKOUT)

        # --- Rule 4: Retest Check ---
        direction: SignalDirection | None = None
        if (
            self._breakout_up
            and latest_bar.low <= self._range_high
            and latest_bar.close > self._range_high
        ):
            direction = SignalDirection.BUY
        elif (
            self._breakout_down
            and latest_bar.high >= self._range_low
            and latest_bar.close < self._range_low
        ):
            direction = SignalDirection.SELL

        if direction is None:
            return self._reject(RejectionReason.NO_RETEST)

        # --- Calculate Entry/Stop/Target ---
        entry = latest_bar.close
        if direction == SignalDirection.BUY:
            sl = self._range_low
            risk_dist = entry - sl
        else:
            sl = self._range_high
            risk_dist = sl - entry

        if risk_dist <= 0.0:
            return self._reject(RejectionReason.NON_POSITIVE_RISK)
        if self.risk_reward <= 0.0:
            return self._reject(RejectionReason.RR_GATE_FAILED)

        reward_dist = risk_dist * self.risk_reward
        tp = entry + reward_dist if direction == SignalDirection.BUY else entry - reward_dist

        self._trade_taken = True

        range_high, range_low = self._range_high, self._range_low
        direction_label = "Bullish" if direction == SignalDirection.BUY else "Bearish"
        volume_note = "volume spike confirmed" if self.require_volume_filter else "volume filter disabled"

        unique_id = uuid.uuid4().hex[:8]
        ts_str = latest_bar.timestamp.strftime("%Y%m%d_%H%M%S_%f")
        setup_id = (
            f"setup_accum_breakout_{market_state.symbol}_{market_state.timeframe.value}_"
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
                f"{self.accumulation_bars}-candle accumulation range",
                f"{direction_label} breakout (body>{self.body_multiplier}x avg, {volume_note})",
                "Retest confirmed",
            ],
            trigger_reason=(
                f"{direction_label} breakout-retest of {self.accumulation_bars}-bar accumulation "
                f"range [{range_low:.5f}, {range_high:.5f}] confirmed at {entry:.5f}"
            ),
            invalidations=[
                "Price closes back inside the accumulation range",
                "Price breaches Stop Loss zone",
            ],
            related_structure_break=None,
            related_order_block=None,
            related_fvg=None,
            timestamp=latest_bar.timestamp,
        )
