"""ManipulationReversalStrategy: the user's 5th strategy ("9:30 NY Manipulation Reversal").

Liquidity grab + trap + reversal, plain-text spec, daily-scoped state
machine intended for M1 bars.

1. The 09:30 NY (DST-aware, session_utils.is_in_session) candle is the
   reference candle. Once it closes, its high/low are frozen for the rest
   of the day.
2. From the NEXT bar onward, there is NO time-of-day cutoff (unlike
   AccumulationBreakoutStrategy/OpeningRangeBreakoutStrategy): every bar is
   checked, indefinitely, until either a scenario completes or the day ends.
   The FIRST bar whose high pierces the reference high (wick-only, close
   irrelevant) starts a BULLISH scenario; the first bar whose low pierces
   the reference low starts a BEARISH scenario. Only one can start per day
   -- whichever condition is met first. If a single bar satisfies both
   simultaneously (a large outside bar), BULLISH is treated as having
   occurred first (an arbitrary but deterministic tie-break; genuinely
   ambiguous within a single bar).
3. Once a scenario starts, a running extremum is tracked (running MAX for
   BULLISH, running MIN for BEARISH), updated on every bar by high/low
   respectively, regardless of that bar's own color. This can update on the
   very same bar the scenario started.
4. The running extremum freezes permanently as the "Trap Point" the moment
   the FIRST bar of the opposite color appears (BULLISH: first red/bearish
   bar, close < open; BEARISH: first green/bullish bar, close > open).
   This can also happen on the same bar the scenario started, if that
   breakout bar is itself the opposite color.
5. After the trap freezes, the strategy waits (arbitrarily long, possibly
   spanning many bars of continued adverse movement) for the FIRST bar that
   reverses color relative to the freezing bar -- i.e. back to the
   scenario's original breakout color. That bar's CLOSE is the entry price,
   simulated as a pending order filled on the next bar via the existing
   entry_zone mechanism (see BacktestEngine).
6. Entry direction matches the ORIGINAL breakout direction, not the trap
   color: BULLISH scenario (reference high broken) -> BUY (after the
   up-trap, down-manipulation, and eventual up-reversal). BEARISH scenario
   -> SELL. This is intentionally the OPPOSITE of what "first opposite-color
   bar = entry" would suggest -- the opposite-color bar only freezes the
   trap; entry requires a SECOND, later reversal back to the original
   breakout color.
7. Risk is anchored to the trap: BUY SL = entry - (trap - entry); SELL
   SL = entry + (entry - trap). TP1 = trap (exactly 1R by construction).
8. If TP1 is touched within `tp_extension_bars` bars of entry, the engine
   (BacktestConfig/TradeSetup conditional_tp_extension_* opt-in) extends
   the target to TP2 (tp_extension_multiple x R) instead of closing;
   otherwise TP stays at TP1 with no special handling (the engine's default
   behavior already covers that case).
9. Only one trade per day; all state resets at the next day's reference
   candle.

Doji handling (close == open, neither red nor green): a doji is treated as
NOT satisfying either color condition -- it can still update the running
extremum (step 3, color-independent) but never freezes the trap and never
triggers entry. Not explicitly covered by the plain-text spec; documented
here as the literal, conservative reading.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.models import SignalDirection, Timeframe
from market_structure.structure_models import MarketState
from strategy.diagnostics import RejectionReason, StrategyDiagnostics
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup
from strategy.session_utils import is_in_session


@dataclass(frozen=True)
class ManipulationReversalConfig:
    """Configuration class for ManipulationReversalStrategy."""

    reference_time: time = time(9, 30)
    session_timezone: str = "America/New_York"
    tp_extension_bars: int = 3
    tp_extension_multiple: float = 2.0


class ManipulationReversalStrategy(TradeSetupStrategy):
    """Ports the user's "9:30 NY Manipulation Reversal" strategy description.

    Evaluates whether the running daily-scoped state (reference candle,
    active scenario, running extremum, frozen trap point, trade-taken guard)
    plus the current M1 bar satisfy a valid manipulation-and-reversal setup,
    without mutating MarketState.
    """

    def __init__(
        self,
        reference_time: time = time(9, 30),
        session_timezone: str = "America/New_York",
        tp_extension_bars: int = 3,
        tp_extension_multiple: float = 2.0,
        config: ManipulationReversalConfig | None = None,
    ) -> None:
        """Initializes the ManipulationReversalStrategy with parameters or config.

        Args:
            reference_time: Local time-of-day (in session_timezone) of the
                single-candle reference bar (default: NY session open).
            session_timezone: IANA timezone name the reference time is
                defined in.
            tp_extension_bars: Bar count window (from entry) within which a
                TP1 touch extends the target to TP2 instead of closing.
            tp_extension_multiple: R-multiple of the extended target (TP2 =
                entry +/- tp_extension_multiple * risk_dist).
            config: ManipulationReversalConfig options overlay.
        """
        if config is not None:
            self.reference_time = config.reference_time
            self.session_timezone = config.session_timezone
            self.tp_extension_bars = config.tp_extension_bars
            self.tp_extension_multiple = config.tp_extension_multiple
        else:
            self.reference_time = reference_time
            self.session_timezone = session_timezone
            self.tp_extension_bars = tp_extension_bars
            self.tp_extension_multiple = tp_extension_multiple

        self._session_tz = ZoneInfo(self.session_timezone)
        # One-minute-wide is_in_session window identifying the single
        # reference candle (robust across hour/day rollover).
        self._reference_window_end = (
            datetime.combine(date(2000, 1, 1), self.reference_time) + timedelta(minutes=1)
        ).time()

        self.diagnostics = StrategyDiagnostics()
        self._reset_day_state()
        self._last_reference_date: date | None = None

    def _reset_day_state(self) -> None:
        """Resets the daily-scoped state (mirrors a new trading day)."""
        self._reference_high: float | None = None
        self._reference_low: float | None = None
        self._reference_ready = False
        self._scenario: SignalDirection | None = None
        self._running_extreme: float | None = None
        self._trap_point: float | None = None
        self._trap_frozen = False
        self._trade_taken_today = False

    def reset(self) -> None:
        """Resets diagnostics and all daily-scoped state (fresh backtest run)."""
        self.diagnostics.reset()
        self._reset_day_state()
        self._last_reference_date = None

    def recommended_max_holding_bars(self, timeframe: Timeframe) -> int | None:
        """Always None: unbounded manipulation window, no session-length concept."""
        return None

    def _reject(self, reason: RejectionReason) -> None:
        """Records a rejection reason and returns None (for use in `return self._reject(...)`)."""
        self.diagnostics.record_rejection(reason)
        return None

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates rules for the manipulation-reversal setup.

        Required checks:
        1. Reference (09:30) candle captured for today
        2. No trade already taken today
        3. A scenario has started (reference high/low pierced)
        4. Trap point has frozen (first opposite-color bar since scenario start)
        5. Reversal bar confirms (first bar back to the scenario's original color)
        6. Positive risk distance
        """
        self.diagnostics.record_evaluation()

        latest_bar = market_state.get_latest_bar()
        if latest_bar is None:
            return self._reject(RejectionReason.NO_LATEST_BAR)

        local_dt = latest_bar.timestamp.astimezone(self._session_tz)

        # --- Rule 1: Reference candle capture / day reset ---
        if is_in_session(
            latest_bar.timestamp, self.reference_time, self._reference_window_end, self._session_tz
        ):
            if local_dt.date() != self._last_reference_date:
                self._reset_day_state()
                self._last_reference_date = local_dt.date()
                self._reference_high = latest_bar.high
                self._reference_low = latest_bar.low
                self._reference_ready = True
            return self._reject(RejectionReason.REFERENCE_CANDLE_NOT_READY)

        if not self._reference_ready:
            return self._reject(RejectionReason.REFERENCE_CANDLE_NOT_READY)

        # --- Rule 2: One Trade Per Day Guard ---
        if self._trade_taken_today:
            return self._reject(RejectionReason.TRADE_ALREADY_TAKEN)

        is_red = latest_bar.close < latest_bar.open
        is_green = latest_bar.close > latest_bar.open

        # --- Rule 3: Scenario start (breakout of reference high/low) ---
        if self._scenario is None:
            bullish_break = latest_bar.high > self._reference_high
            bearish_break = latest_bar.low < self._reference_low
            if bullish_break:
                self._scenario = SignalDirection.BUY
                self._running_extreme = latest_bar.high
            elif bearish_break:
                self._scenario = SignalDirection.SELL
                self._running_extreme = latest_bar.low
            else:
                return self._reject(RejectionReason.NO_MANIPULATION_SCENARIO)

        # --- Rule 4: Running extremum tracking + trap freeze ---
        if not self._trap_frozen:
            if self._scenario == SignalDirection.BUY:
                if latest_bar.high > self._running_extreme:
                    self._running_extreme = latest_bar.high
                if is_red:
                    self._trap_point = self._running_extreme
                    self._trap_frozen = True
            else:
                if latest_bar.low < self._running_extreme:
                    self._running_extreme = latest_bar.low
                if is_green:
                    self._trap_point = self._running_extreme
                    self._trap_frozen = True

            if not self._trap_frozen:
                return self._reject(RejectionReason.TRAP_NOT_FROZEN)
            return self._reject(RejectionReason.NO_REVERSAL_YET)

        # --- Rule 5: Reversal-of-reversal entry trigger ---
        if self._scenario == SignalDirection.BUY:
            if not is_green:
                return self._reject(RejectionReason.NO_REVERSAL_YET)
        else:
            if not is_red:
                return self._reject(RejectionReason.NO_REVERSAL_YET)

        # --- Calculate Entry/Stop/Target ---
        entry = latest_bar.close
        trap = self._trap_point
        direction = self._scenario

        if direction == SignalDirection.BUY:
            risk_dist = trap - entry
            sl = entry - risk_dist
        else:
            risk_dist = entry - trap
            sl = entry + risk_dist

        if risk_dist <= 0.0:
            return self._reject(RejectionReason.NON_POSITIVE_RISK)

        tp1 = trap
        reward_dist_2r = risk_dist * self.tp_extension_multiple
        tp2 = entry + reward_dist_2r if direction == SignalDirection.BUY else entry - reward_dist_2r

        self._trade_taken_today = True

        direction_label = "Bullish" if direction == SignalDirection.BUY else "Bearish"

        unique_id = uuid.uuid4().hex[:8]
        ts_str = latest_bar.timestamp.strftime("%Y%m%d_%H%M%S_%f")
        setup_id = (
            f"setup_manip_reversal_{market_state.symbol}_{market_state.timeframe.value}_"
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
            target_zone=(round(tp1, 5), round(tp1, 5)),
            confidence_score=1.0,
            confluence=[
                f"{direction_label} scenario: reference candle broken at {self._reference_high if direction == SignalDirection.BUY else self._reference_low:.5f}",
                f"Trap Point {trap:.5f} frozen on first opposite-color bar",
                f"Reversal confirmed, entry at {entry:.5f} (TP1=1R={tp1:.5f}, "
                f"conditional TP2={self.tp_extension_multiple}R={tp2:.5f} within "
                f"{self.tp_extension_bars} bars)",
            ],
            trigger_reason=(
                f"{direction_label} manipulation reversal: trap={trap:.5f}, entry={entry:.5f}, "
                f"SL={sl:.5f}, TP1={tp1:.5f}"
            ),
            invalidations=[
                "Price breaches Stop Loss zone",
            ],
            related_structure_break=None,
            related_order_block=None,
            related_fvg=None,
            timestamp=latest_bar.timestamp,
            strategy_name=self.__class__.__name__,
            conditional_tp_extension_bars=self.tp_extension_bars,
            conditional_tp_extension_price=round(tp2, 5),
        )
