"""OpeningRangeBreakoutStrategy: the user's 3rd strategy (plain-text spec,
no Pine Script source), ported directly to Python.

Logic (daily-scoped state machine, intended for M1 bars):
1. At the NY session open (default 09:30), the first `range_bars` (default 5)
   M1 candles' combined high/low form the opening range.
2. From the bar right after the range completes, for the REST OF THE DAY
   (no time-of-day window restriction), every new bar is checked for a
   breakout: an above-average volume (>volume_multiplier x SMA-N volume)
   candle that both exits the range AND closes on that same side (close
   beyond range_high for a bullish breakout, or beyond range_low for a
   bearish one).
3. Stop loss is anchored to the FIRST opening candle specifically (not the
   whole range's high/low): the first candle's low for a bullish breakout,
   its high for a bearish one -- a deliberate design choice distinct from
   AccumulationBreakoutStrategy/NasdaqMidlineSweepStrategy, where the stop
   is the range boundary itself.
4. Only one trade per day; all state resets at the next session open.

Like AccumulationBreakoutStrategy and NasdaqMidlineSweepStrategy, this
strategy is inherently STATEFUL across evaluate() calls and uses the same
DST-aware `zoneinfo` conversion those strategies get from
strategy/session_utils.py. It does not reuse `is_in_session` directly,
though, since session_start here is a single one-sided boundary (no
session_end -- the breakout/volume check runs at any time of day once the
range is armed), not `is_in_session`'s two-sided window.

Day-reset note: the new-day trigger is the local CALENDAR DATE (in
session_timezone) changing versus the last bar that started a range,
compared *at* the first bar on/after session_start -- not a simple
"was the previous bar before session_start" transition flag. The latter
would misfire after a gap that skips straight over the pre-session-start
window (e.g. a weekend reopen at a time already past session_start): the
transition flag would still read "already past session_start" from the
prior session and never dip back to false, silently skipping that day's
reset. Tracking the calendar date directly is immune to this regardless of
how a day's bars are gapped.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from core.models import SignalDirection
from market_structure.structure_models import MarketState
from strategy.diagnostics import RejectionReason, StrategyDiagnostics
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup


@dataclass(frozen=True)
class OpeningRangeBreakoutConfig:
    """Configuration class for OpeningRangeBreakoutStrategy."""

    volume_multiplier: float = 1.5
    volume_lookback: int = 20
    risk_reward: float = 3.0
    range_bars: int = 5
    session_start: time = time(9, 30)
    session_timezone: str = "America/New_York"


class OpeningRangeBreakoutStrategy(TradeSetupStrategy):
    """Ports the user's Opening Range Breakout strategy description.

    Evaluates whether the running daily-scoped state (opening range, first-
    candle SL anchor, trade-taken guard) plus the current M1 bar satisfy a
    valid volume-confirmed breakout, without mutating MarketState.
    """

    def __init__(
        self,
        volume_multiplier: float = 1.5,
        volume_lookback: int = 20,
        risk_reward: float = 3.0,
        range_bars: int = 5,
        session_start: time = time(9, 30),
        session_timezone: str = "America/New_York",
        config: OpeningRangeBreakoutConfig | None = None,
    ) -> None:
        """Initializes the OpeningRangeBreakoutStrategy with parameters or config.

        Args:
            volume_multiplier: Volume spike threshold as a multiple of SMA-N volume.
            volume_lookback: Lookback window (in bars) for the volume moving average.
            risk_reward: Fixed reward:risk multiple applied to the stop distance.
            range_bars: Number of opening candles combined into the range.
            session_start: Local time-of-day (in session_timezone) the opening
                range begins forming.
            session_timezone: IANA timezone name the session is defined in
                (default: the NY exchange open).
            config: OpeningRangeBreakoutConfig options overlay.
        """
        if config is not None:
            self.volume_multiplier = config.volume_multiplier
            self.volume_lookback = config.volume_lookback
            self.risk_reward = config.risk_reward
            self.range_bars = config.range_bars
            self.session_start = config.session_start
            self.session_timezone = config.session_timezone
        else:
            self.volume_multiplier = volume_multiplier
            self.volume_lookback = volume_lookback
            self.risk_reward = risk_reward
            self.range_bars = range_bars
            self.session_start = session_start
            self.session_timezone = session_timezone

        self._session_tz = ZoneInfo(self.session_timezone)
        self.diagnostics = StrategyDiagnostics()
        self._reset_day_state()
        self._last_range_date: date | None = None

    def _reset_day_state(self) -> None:
        """Resets the daily-scoped state (mirrors a new trading day)."""
        self._range_count = 0
        self._range_high: float | None = None
        self._range_low: float | None = None
        self._first_bar_high: float | None = None
        self._first_bar_low: float | None = None
        self._range_ready = False
        self._trade_taken_today = False

    def reset(self) -> None:
        """Resets diagnostics and all daily-scoped state (fresh backtest run)."""
        self.diagnostics.reset()
        self._reset_day_state()
        self._last_range_date = None

    def _reject(self, reason: RejectionReason) -> None:
        """Records a rejection reason and returns None (for use in `return self._reject(...)`)."""
        self.diagnostics.record_rejection(reason)
        return None

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates rules for the opening-range breakout setup.

        Required checks:
        1. Opening range is ready (range_bars candles accumulated from session_start)
        2. No trade already taken today
        3. Candle exits the range AND closes on that same side
        4. Above-average volume (>volume_multiplier x SMA-N volume)
        5. Positive risk distance and a valid risk_reward configuration
        """
        self.diagnostics.record_evaluation()

        latest_bar = market_state.get_latest_bar()
        if latest_bar is None:
            return self._reject(RejectionReason.NO_LATEST_BAR)

        local_dt = latest_bar.timestamp.astimezone(self._session_tz)
        at_or_after_session_start = local_dt.time() >= self.session_start
        if at_or_after_session_start and local_dt.date() != self._last_range_date:
            self._reset_day_state()
            self._last_range_date = local_dt.date()

        # --- Rule 1: Opening Range Accumulation ---
        if not self._range_ready and at_or_after_session_start:
            if self._range_count == 0:
                self._first_bar_high = latest_bar.high
                self._first_bar_low = latest_bar.low
                self._range_high = latest_bar.high
                self._range_low = latest_bar.low
            else:
                self._range_high = max(self._range_high, latest_bar.high)
                self._range_low = min(self._range_low, latest_bar.low)
            self._range_count += 1
            if self._range_count == self.range_bars:
                self._range_ready = True

        if not self._range_ready:
            return self._reject(RejectionReason.RANGE_NOT_READY)

        # --- Rule 2: One Trade Per Day Guard ---
        if self._trade_taken_today:
            return self._reject(RejectionReason.TRADE_ALREADY_TAKEN_TODAY)

        # --- Rule 3: Breakout + Close-Side Check ---
        attempted_up = latest_bar.high > self._range_high
        attempted_down = latest_bar.low < self._range_low
        bullish_break = latest_bar.close > self._range_high
        bearish_break = latest_bar.close < self._range_low

        if not attempted_up and not attempted_down:
            return self._reject(RejectionReason.NO_BREAKOUT)
        if not bullish_break and not bearish_break:
            return self._reject(RejectionReason.WRONG_CLOSE_SIDE)

        direction = SignalDirection.BUY if bullish_break else SignalDirection.SELL

        # --- Rule 4: Volume Spike Check ---
        recent_bars = market_state.bars_view()
        window = recent_bars[-self.volume_lookback :]
        have_full_window = len(recent_bars) >= self.volume_lookback
        avg_volume = sum(b.volume for b in window) / len(window) if window else 0.0
        volume_ok = have_full_window and latest_bar.volume > avg_volume * self.volume_multiplier

        if not volume_ok:
            return self._reject(RejectionReason.NO_VOLUME_SPIKE)

        # --- Calculate Entry/Stop/Target ---
        entry = latest_bar.close
        if direction == SignalDirection.BUY:
            sl = self._first_bar_low
            risk_dist = entry - sl
        else:
            sl = self._first_bar_high
            risk_dist = sl - entry

        if risk_dist <= 0.0:
            return self._reject(RejectionReason.NON_POSITIVE_RISK)
        if self.risk_reward <= 0.0:
            return self._reject(RejectionReason.RR_GATE_FAILED)

        reward_dist = risk_dist * self.risk_reward
        tp = entry + reward_dist if direction == SignalDirection.BUY else entry - reward_dist

        self._trade_taken_today = True

        range_high, range_low = self._range_high, self._range_low
        direction_label = "Bullish" if direction == SignalDirection.BUY else "Bearish"
        volume_ratio = latest_bar.volume / avg_volume if avg_volume > 0 else float("inf")

        unique_id = uuid.uuid4().hex[:8]
        ts_str = latest_bar.timestamp.strftime("%Y%m%d_%H%M%S_%f")
        setup_id = (
            f"setup_orb_{market_state.symbol}_{market_state.timeframe.value}_"
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
                f"{self.range_bars}-bar opening range [{range_low:.5f}, {range_high:.5f}]",
                f"{direction_label} breakout with confirmed close",
                f"Volume spike ({volume_ratio:.2f}x avg)",
            ],
            trigger_reason=(
                f"{direction_label} opening-range breakout: range=[{range_low:.5f}, "
                f"{range_high:.5f}] (first {self.range_bars} bars), closed at {entry:.5f}, "
                f"volume {volume_ratio:.2f}x avg"
            ),
            invalidations=[
                "Price closes back inside the opening range",
                "Price breaches Stop Loss zone",
            ],
            related_structure_break=None,
            related_order_block=None,
            related_fvg=None,
            timestamp=latest_bar.timestamp,
            strategy_name=self.__class__.__name__,
        )
