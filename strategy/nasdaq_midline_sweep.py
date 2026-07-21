"""NasdaqMidlineSweepStrategy: port of the user's "NASDAQ Midline Sweep
Strategy v2 (Filtered)" TradingView Pine Script strategy.

Original Pine logic (daily-scoped state machine, run natively on M5 -- see
"Multi-timeframe" note below):
1. During a fixed build session (default "0930-0950" NY), accumulate the
   running average of closes.
2. At the bar the build session ends, freeze that average as `mid`, and
   derive a fixed zone [`mid` - range_size, `mid` + range_size].
3. On any later bar (same day), a long signal fires when: price closes well
   above the midline (> mid + mid_buffer), the bar's low dipped below the
   upper zone edge but its close reclaimed above it (a sweep+reclaim of the
   upper edge), and the candle's body is a strong displacement (>1.2x SMA-20
   body). The short signal mirrors this around the lower edge.
4. Only one trade per day; all state resets at the next build session.

Multi-timeframe note: the original Pine script pulls 5-minute data via
`request.security(syminfo.tickerid, "5", ...)` regardless of the chart's own
timeframe, while its `avgBody` displacement filter is computed from the
*chart's own* (potentially different-timeframe) bars -- an inconsistency in
the source script if the chart isn't already M5. Per the user's decision,
this strategy is ported to run **natively on M5** (single MarketState, no
"chart timeframe" concept): this removes the request.security dependency
entirely (this codebase's TradeSetupStrategy interface takes one MarketState
at a time) and resolves the avgBody ambiguity, since "chart" and "5-minute"
bars become the same series. It also better matches the 20-minute build
session, which produces a meaningfully coarse 1-2 bar average on any
timeframe coarser than M5.

Day-reset note (Bug #50 fix): the original Pine resets on `ta.change(time("D"))`
(calendar day change). This is ported as a calendar-date comparison at/after
build_session_start (mirroring OpeningRangeBreakoutStrategy's fix), not a
build-session-entry transition flag: an earlier version reset on
`in_build_session and not previously_in_build_session`, which never fires if
a data gap skips straight over the entire build window on a given day (e.g.
a broker interruption spanning build_session_start..build_session_end) --
the transition edge is never observed, so that day's reset silently never
happens and the previous day's frozen zone/trade-taken flag would carry over
indefinitely. Tracking "have I already reset for this calendar date" directly
is immune to this regardless of how a day's bars are gapped. The midline
freeze (previously also transition-based, on build-session *exit*) is
ported the same way: it fires once we are at/after build_session_end for the
already-reset date, rather than needing to observe the exact in-session ->
out-of-session edge.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from core.models import SignalDirection, Timeframe
from core.validation import require_non_negative, require_positive
from market_structure.structure_models import MarketState
from strategy.diagnostics import RejectionReason, StrategyDiagnostics
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup
from strategy.risk_reward import calculate_take_profit
from strategy.session_utils import is_in_session, session_length_in_bars


@dataclass(frozen=True)
class NasdaqMidlineSweepConfig:
    """Configuration class for NasdaqMidlineSweepStrategy."""

    range_size: float = 10.0
    risk_reward: float = 2.0
    mid_buffer: float = 5.0
    body_multiplier: float = 1.5
    sma_period: int = 20
    build_session_start: time = time(9, 30)
    build_session_end: time = time(9, 50)
    session_timezone: str = "America/New_York"
    day_session_end: time | None = None

    def __post_init__(self) -> None:
        """Validates parameter ranges.

        Raises:
            ValueError: If range_size, risk_reward, body_multiplier, or
                sma_period is not strictly positive, mid_buffer is negative,
                or build_session_start is not strictly before
                build_session_end (Bug #60).
        """
        require_positive(self.range_size, "range_size")
        require_positive(self.risk_reward, "risk_reward")
        require_non_negative(self.mid_buffer, "mid_buffer")
        require_positive(self.body_multiplier, "body_multiplier")
        require_positive(self.sma_period, "sma_period")
        if self.build_session_start >= self.build_session_end:
            raise ValueError(
                f"build_session_start ({self.build_session_start}) must be strictly "
                f"before build_session_end ({self.build_session_end}); the midline "
                "would never freeze, and the strategy would generate zero signals forever."
            )


class NasdaqMidlineSweepStrategy(TradeSetupStrategy):
    """Ports the "NASDAQ Midline Sweep Strategy v2 (Filtered)" Pine script.

    Evaluates whether the running daily-scoped state (fixed midline/zone,
    trade-taken guard) plus the current M5 bar satisfy a valid sweep+reclaim
    setup, without mutating MarketState.
    """

    def __init__(
        self,
        range_size: float = 10.0,
        risk_reward: float = 2.0,
        mid_buffer: float = 5.0,
        body_multiplier: float = 1.5,
        sma_period: int = 20,
        build_session_start: time = time(9, 30),
        build_session_end: time = time(9, 50),
        session_timezone: str = "America/New_York",
        day_session_end: time | None = None,
        config: NasdaqMidlineSweepConfig | None = None,
    ) -> None:
        """Initializes the NasdaqMidlineSweepStrategy with parameters or config.

        Args:
            range_size: Fixed +/- offset from the midline defining the zone edges.
            risk_reward: Fixed reward:risk multiple applied to the stop distance.
            mid_buffer: Minimum distance beyond the midline the close must reach
                before a sweep on that side is considered.
            body_multiplier: Breakout candle body threshold as a multiple of SMA-20 body.
            sma_period: Lookback window (in M5 bars) for the body moving average.
            build_session_start: Local time-of-day (in session_timezone) the
                midline-building window opens (inclusive).
            build_session_end: Local time-of-day (in session_timezone) the
                midline-building window closes (exclusive); the midline is
                frozen on the first bar after this.
            session_timezone: IANA timezone name the build session is defined
                in (default: the NY exchange session the original Pine script
                scans).
            day_session_end: Local time-of-day (in session_timezone) the traded
                instrument's daily session is considered to close, used only by
                recommended_max_holding_bars() to derive a bar count from
                [build_session_start, day_session_end). Default None preserves
                the pre-existing unlimited-holding behavior (returns None) --
                set explicitly once the real instrument's hours are known (a
                classic cash-market close, or a CFD/index/metal's much longer
                session), since this can't be assumed from the strategy alone.
            config: NasdaqMidlineSweepConfig options overlay.

        Raises:
            TypeError: If config is provided but is not a
                NasdaqMidlineSweepConfig.
            ValueError: If any parameter fails NasdaqMidlineSweepConfig's
                validity constraints (see its __post_init__).
        """
        if config is not None:
            if not isinstance(config, NasdaqMidlineSweepConfig):
                raise TypeError(
                    f"config must be a NasdaqMidlineSweepConfig, got {type(config).__name__}"
                )
        else:
            config = NasdaqMidlineSweepConfig(
                range_size=range_size,
                risk_reward=risk_reward,
                mid_buffer=mid_buffer,
                body_multiplier=body_multiplier,
                sma_period=sma_period,
                build_session_start=build_session_start,
                build_session_end=build_session_end,
                session_timezone=session_timezone,
                day_session_end=day_session_end,
            )

        self.range_size = config.range_size
        self.risk_reward = config.risk_reward
        self.mid_buffer = config.mid_buffer
        self.body_multiplier = config.body_multiplier
        self.sma_period = config.sma_period
        self.build_session_start = config.build_session_start
        self.build_session_end = config.build_session_end
        self.session_timezone = config.session_timezone
        self.day_session_end = config.day_session_end

        self._session_tz = ZoneInfo(self.session_timezone)
        self.diagnostics = StrategyDiagnostics()
        self._reset_day_state()
        self._last_build_date: date | None = None

    def _reset_day_state(self) -> None:
        """Resets the Pine `var` daily-scoped state (mirrors `if newDay`)."""
        self._sum_close = 0.0
        self._count_close = 0
        self._mid: float | None = None
        self._upper: float | None = None
        self._lower: float | None = None
        self._zone_ready = False
        self._trade_taken = False

    def reset(self) -> None:
        """Resets diagnostics and all daily-scoped state (fresh backtest run)."""
        self.diagnostics.reset()
        self._reset_day_state()
        self._last_build_date = None

    def recommended_max_holding_bars(self, timeframe: Timeframe) -> int | None:
        """Bar count for [build_session_start, day_session_end) if
        day_session_end is set, otherwise None (unlimited holding, unchanged
        default).
        """
        if self.day_session_end is None:
            return None
        return session_length_in_bars(self.build_session_start, self.day_session_end, timeframe)

    def _reject(self, reason: RejectionReason) -> None:
        """Records a rejection reason and returns None (for use in `return self._reject(...)`)."""
        self.diagnostics.record_rejection(reason)
        return None

    def _in_build_session(self, timestamp: datetime) -> bool:
        """Whether the given timestamp falls in [build_session_start, build_session_end)."""
        return is_in_session(
            timestamp, self.build_session_start, self.build_session_end, self._session_tz
        )

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates rules for the fixed-range midline sweep setup.

        Required checks:
        1. Daily zone is ready (build session has completed and frozen a midline)
        2. No trade already taken this day
        3. Close is beyond the midline by at least mid_buffer, on one side
        4. That side's zone edge was swept (low/high crossed it) and reclaimed
        5. Strong displacement body (>body_multiplier x SMA-N body)
        6. Positive risk distance and a valid risk_reward configuration
        """
        self.diagnostics.record_evaluation()

        latest_bar = market_state.get_latest_bar()
        if latest_bar is None:
            return self._reject(RejectionReason.NO_LATEST_BAR)

        local_dt = latest_bar.timestamp.astimezone(self._session_tz)
        at_or_after_build_start = local_dt.time() >= self.build_session_start
        if at_or_after_build_start and local_dt.date() != self._last_build_date:
            self._reset_day_state()
            self._last_build_date = local_dt.date()

        in_build_session = self._in_build_session(latest_bar.timestamp)

        # --- Rule 1a: Accumulate the build-session midline ---
        if in_build_session:
            self._sum_close += latest_bar.close
            self._count_close += 1

        # --- Rule 1b: Freeze the midline/zone once at/after build-session end for today ---
        at_or_after_build_end = local_dt.time() >= self.build_session_end
        if (
            not self._zone_ready
            and at_or_after_build_end
            and local_dt.date() == self._last_build_date
            and self._count_close > 0
        ):
            self._mid = self._sum_close / self._count_close
            self._upper = self._mid + self.range_size
            self._lower = self._mid - self.range_size
            self._zone_ready = True

        if not self._zone_ready:
            return self._reject(RejectionReason.ZONE_NOT_READY)

        # --- Rule 2: One Trade Per Day Guard ---
        if self._trade_taken:
            return self._reject(RejectionReason.TRADE_ALREADY_TAKEN)

        # --- Rule 3: Mid-Buffer Side Filter ---
        long_ok = latest_bar.close > self._mid + self.mid_buffer
        short_ok = latest_bar.close < self._mid - self.mid_buffer

        if not long_ok and not short_ok:
            return self._reject(RejectionReason.WRONG_SIDE_OF_MID)

        # --- Rule 4: Sweep+Reclaim Check ---
        long_sweep = latest_bar.low < self._upper and latest_bar.close > self._upper
        short_sweep = latest_bar.high > self._lower and latest_bar.close < self._lower

        if long_ok and not long_sweep:
            return self._reject(RejectionReason.NO_SWEEP)
        if short_ok and not short_sweep:
            return self._reject(RejectionReason.NO_SWEEP)

        # --- Rule 5: Displacement Check ---
        recent_bars = market_state.bars_view()
        window = recent_bars[-self.sma_period :]
        have_full_window = len(recent_bars) >= self.sma_period
        body = abs(latest_bar.close - latest_bar.open)
        avg_body = sum(abs(b.close - b.open) for b in window) / len(window) if window else 0.0
        strong_move = have_full_window and body > avg_body * self.body_multiplier

        if not strong_move:
            return self._reject(RejectionReason.NO_DISPLACEMENT)

        direction = SignalDirection.BUY if (long_ok and long_sweep) else SignalDirection.SELL

        # --- Calculate Entry/Stop/Target ---
        entry = latest_bar.close
        if direction == SignalDirection.BUY:
            sl = self._lower
            risk_dist = entry - sl
        else:
            sl = self._upper
            risk_dist = sl - entry

        if risk_dist <= 0.0:
            return self._reject(RejectionReason.NON_POSITIVE_RISK)
        if self.risk_reward <= 0.0:
            return self._reject(RejectionReason.RR_GATE_FAILED)

        tp = calculate_take_profit(entry, direction, risk_dist, self.risk_reward)

        self._trade_taken = True

        mid, upper, lower = self._mid, self._upper, self._lower
        direction_label = "Bullish" if direction == SignalDirection.BUY else "Bearish"
        edge_label = "upper" if direction == SignalDirection.BUY else "lower"
        edge_price = upper if direction == SignalDirection.BUY else lower

        unique_id = uuid.uuid4().hex[:8]
        ts_str = latest_bar.timestamp.strftime("%Y%m%d_%H%M%S_%f")
        setup_id = (
            f"setup_midline_sweep_{market_state.symbol}_{market_state.timeframe.value}_"
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
                "Fixed opening-range midline (build session)",
                f"{direction_label} sweep+reclaim of {edge_label} zone boundary",
                f"Displacement confirmed (body>{self.body_multiplier}x avg)",
            ],
            trigger_reason=(
                f"{direction_label} midline sweep: {edge_label} boundary ({edge_price:.5f}) "
                f"swept and reclaimed at {entry:.5f}, mid={mid:.5f}"
            ),
            invalidations=[
                "Price closes back across the midline",
                "Price breaches Stop Loss zone",
            ],
            related_structure_break=None,
            related_order_block=None,
            related_fvg=None,
            timestamp=latest_bar.timestamp,
            strategy_name=self.__class__.__name__,
        )
