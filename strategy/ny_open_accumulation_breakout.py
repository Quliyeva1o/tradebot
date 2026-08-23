"""NyOpenAccumulationBreakoutStrategy: live port of the "NY-Open Akkumulyasiya
Sındırma + Retest" strategy that was iteratively specified and validated
against 2 years of real USTEC M1 history in scripts/accumulation_breakout_backtest.py
(see that module's docstring for the full rule derivation, and the
conversation that shaped it -- daily bias state machine, per-day rolling
compression baseline, two-level retest, liquidity-hunt TP capped at N R).

Cross-timeframe inputs CANNOT be derived from the M1-only MarketState this
strategy's evaluate() receives (TradeSetupStrategy's contract is exactly one
MarketState): daily HTF structure (bias) and PDH/PDL/session-liquidity/swing
targets all need D1 (or wider intraday-session) bars. Rather than silently
resampling thousands of M1 bars inside evaluate() on every tick, the caller
(a live-loop script, see run_live_accumulation_breakout.py) is responsible
for fetching D1/session bars ONCE per NY calendar day and pushing the result
in via set_daily_context() before the day's first evaluate() call. If no
context has been set for the CURRENT NY date, evaluate() safely rejects
every bar that day (NO_TREND) rather than trading on a stale prior day's
bias -- see _current_date/_context_date staleness check below.

Safety: this class only ever RETURNS a TradeSetup candidate; it never places
an order. Wiring a TradeSetup into a real (even demo) order goes through
TradeManager/MT5Broker exactly like every other strategy in this codebase
(see run_live_demo.py) -- this file adds no new order-placement path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.models import Bar, SignalDirection, Timeframe
from core.validation import require_positive
from market_structure.structure_models import MarketState
from strategy.diagnostics import RejectionReason, StrategyDiagnostics
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class DailyContext:
    """Cross-timeframe inputs the caller computes once per NY day from D1/
    session bars and pushes in via set_daily_context() -- see module
    docstring. All optional fields being None just means that liquidity
    candidate is unavailable today (e.g. no swing found in the lookback);
    the strategy still runs with whatever subset is present.

    Attributes:
        for_date: The NY calendar date this context applies to. evaluate()
            only trusts _daily_bias when the bar's own NY date matches this
            (see the staleness check in evaluate()).
        bias: "LONG" or "SHORT" -- the day's HTF-structure-derived bias
            (the sole gate, per the backtest's resolved design: see that
            script's STEP 1 docstring for why premium/discount and
            liquidity-sweep confluence are informational, not additional
            hard gates, in this version).
        recent_avg_range: Trailing ~5-day median single-M1-bar range, used
            as the LOCAL compression baseline (see
            scripts/accumulation_breakout_backtest.py's ROLLING_DAYS
            baseline -- NOT a fixed global constant).
        pdh, pdl: Previous trading day's cash-session [09:30-16:00 NY] high/low.
        asia_high, asia_low: Prior-evening [20:00-00:00 NY] session high/low.
        london_high, london_low: Today's [02:00-05:00 NY] session high/low.
        nearest_swing_high, nearest_swing_low: Nearest unmitigated daily
            fractal swing high/low within the trailing lookback (see
            scripts/bias_liquidity_backtest.py's nearest_unmitigated_swing).
    """

    for_date: date
    bias: str
    recent_avg_range: float
    pdh: float | None = None
    pdl: float | None = None
    asia_high: float | None = None
    asia_low: float | None = None
    london_high: float | None = None
    london_low: float | None = None
    nearest_swing_high: float | None = None
    nearest_swing_low: float | None = None


@dataclass(frozen=True)
class NyOpenAccumulationBreakoutConfig:
    """Configuration for NyOpenAccumulationBreakoutStrategy."""

    window_start: time = time(9, 30)
    window_end: time = time(10, 0)
    min_accum_candles: int = 2
    max_accum_candles: int = 8
    compression_multiplier: float = 4.0
    max_body_fraction: float = 0.5
    breakout_search_candles: int = 10
    retest_window_candles: int = 5
    sl_buffer_points: float = 10.0
    min_rr: float = 2.0
    max_rr_cap: float | None = 3.0

    def __post_init__(self) -> None:
        """Validates parameter ranges.

        Raises:
            ValueError: If any *_candles/multiplier/fraction/buffer/rr field
                is not strictly positive, or window_start is not strictly
                before window_end.
        """
        require_positive(self.min_accum_candles, "min_accum_candles")
        require_positive(self.max_accum_candles, "max_accum_candles")
        require_positive(self.compression_multiplier, "compression_multiplier")
        require_positive(self.max_body_fraction, "max_body_fraction")
        require_positive(self.breakout_search_candles, "breakout_search_candles")
        require_positive(self.retest_window_candles, "retest_window_candles")
        require_positive(self.sl_buffer_points, "sl_buffer_points")
        require_positive(self.min_rr, "min_rr")
        if self.max_rr_cap is not None:
            require_positive(self.max_rr_cap, "max_rr_cap")
        if self.window_start >= self.window_end:
            raise ValueError(
                f"window_start ({self.window_start}) must be strictly before "
                f"window_end ({self.window_end})."
            )
        if self.max_accum_candles < self.min_accum_candles:
            raise ValueError("max_accum_candles must be >= min_accum_candles.")


def compute_daily_context(
    bars: list[Bar],
    for_date: date,
    structure_lookback_days: int = 20,
    pd_lookback_days: int = 10,
    swing_lookback_days: int = 15,
    rolling_baseline_days: int = 5,
) -> DailyContext | None:
    """Derives `for_date`'s DailyContext from a chronological M1 bar history.

    Intended for a live-loop caller to run ONCE per NY morning (before the
    day's first evaluate() call), from the SAME M1 fetch it already pulls
    for the strategy itself (a sufficiently long lookback, e.g.
    structure_lookback_days + a few days of buffer, covers both needs --
    no separate D1 fetch required). Only uses bars strictly BEFORE
    `for_date` for bias/PDH-PDL/swings (no lookahead), and `for_date`'s own
    bars up to 09:30 for the Asia/London/pre-open-sweep pieces.

    Returns None if there isn't enough history yet (fewer than
    structure_lookback_days prior trading days present in `bars`) --
    callers should treat that as "no context available today" and simply
    not call set_daily_context(), which correctly makes evaluate() reject
    every bar that day (NO_TREND) rather than trade on incomplete inputs.
    """
    by_date: dict[date, list[Bar]] = {}
    for b in bars:
        by_date.setdefault(b.timestamp.astimezone(NY).date(), []).append(b)
    trading_days = sorted(d for d in by_date if d.weekday() < 5 and d <= for_date)
    if for_date not in trading_days:
        trading_days.append(for_date)
        trading_days.sort()
    if for_date not in by_date:
        return None
    idx = trading_days.index(for_date)
    if idx < structure_lookback_days:
        return None

    def day_hl(d: date) -> tuple[float, float]:
        day_bars = by_date[d]
        return max(b.high for b in day_bars), min(b.low for b in day_bars)

    # --- HTF structure (BOS/CHoCH state machine, 3-bar fractal daily swings) ---
    prior_days = trading_days[:idx]
    state: str | None = None
    last_swing_high: float | None = None
    last_swing_low: float | None = None
    for i in range(2, len(prior_days)):
        d0, d1, d2 = prior_days[i - 2], prior_days[i - 1], prior_days[i]
        h0, l0 = day_hl(d0)
        h1, l1 = day_hl(d1)
        h2, l2 = day_hl(d2)
        if h1 > h0 and h1 > h2:
            last_swing_high = h1
        if l1 < l0 and l1 < l2:
            last_swing_low = l1
        _, close = day_hl(d2)[0], by_date[d2][-1].close
        if last_swing_high is not None and close > last_swing_high:
            state = "BULLISH"
        if last_swing_low is not None and close < last_swing_low:
            state = "BEARISH"
    if state is None:
        return None
    bias = "LONG" if state == "BULLISH" else "SHORT"

    # --- Rolling compression baseline (trailing N days, all-session bars) ---
    baseline_days = prior_days[-rolling_baseline_days:]
    baseline_ranges = [b.high - b.low for dd in baseline_days for b in by_date[dd]]
    if not baseline_ranges:
        return None
    recent_avg_range = sorted(baseline_ranges)[len(baseline_ranges) // 2]

    # --- PDH/PDL (previous trading day's cash session) ---
    prev_day = prior_days[-1]
    prev_cash = [b for b in by_date[prev_day] if time(9, 30) <= b.timestamp.astimezone(NY).time() < time(16, 0)]
    pdh = max((b.high for b in prev_cash), default=None)
    pdl = min((b.low for b in prev_cash), default=None)

    # --- Asia / London sessions ---
    asia_start = datetime.combine(for_date, time(20, 0), NY) - timedelta(days=1)
    asia_end = datetime.combine(for_date, time(0, 0), NY)
    asia_bars = [b for b in bars if asia_start <= b.timestamp.astimezone(NY) < asia_end]
    asia_high = max((b.high for b in asia_bars), default=None)
    asia_low = min((b.low for b in asia_bars), default=None)

    london_bars = [b for b in by_date[for_date] if time(2, 0) <= b.timestamp.astimezone(NY).time() < time(5, 0)]
    london_high = max((b.high for b in london_bars), default=None)
    london_low = min((b.low for b in london_bars), default=None)

    # --- Nearest unmitigated daily swing high/low (trailing lookback) ---
    swing_window = prior_days[-swing_lookback_days:]
    swing_lows: dict[date, float] = {}
    swing_highs: dict[date, float] = {}
    for i in range(1, len(swing_window) - 1):
        h0, l0 = day_hl(swing_window[i - 1])
        h1, l1 = day_hl(swing_window[i])
        h2, l2 = day_hl(swing_window[i + 1])
        if l1 < l0 and l1 < l2:
            swing_lows[swing_window[i]] = l1
        if h1 > h0 and h1 > h2:
            swing_highs[swing_window[i]] = h1

    def nearest_unmitigated(pool: dict[date, float], direction_short: bool) -> float | None:
        candidates = []
        for d, level in pool.items():
            later_days = [dd for dd in prior_days if dd > d]
            mitigated = False
            for later in later_days:
                h, l = day_hl(later)
                if direction_short and l <= level:
                    mitigated = True
                    break
                if not direction_short and h >= level:
                    mitigated = True
                    break
            if not mitigated:
                candidates.append(level)
        if not candidates:
            return None
        return max(candidates) if direction_short else min(candidates)

    nearest_swing_low = nearest_unmitigated(swing_lows, direction_short=True)
    nearest_swing_high = nearest_unmitigated(swing_highs, direction_short=False)

    return DailyContext(
        for_date=for_date,
        bias=bias,
        recent_avg_range=recent_avg_range,
        pdh=pdh, pdl=pdl,
        asia_high=asia_high, asia_low=asia_low,
        london_high=london_high, london_low=london_low,
        nearest_swing_high=nearest_swing_high, nearest_swing_low=nearest_swing_low,
    )


def _is_engulfing(prev_bar: Bar, bar: Bar) -> bool:
    """A candle's body fully covers the immediately preceding candle's body."""
    prev_lo, prev_hi = min(prev_bar.open, prev_bar.close), max(prev_bar.open, prev_bar.close)
    lo, hi = min(bar.open, bar.close), max(bar.open, bar.close)
    return lo <= prev_lo and hi >= prev_hi and bar.close != bar.open


class NyOpenAccumulationBreakoutStrategy(TradeSetupStrategy):
    """Live port of the accumulation-breakout+retest strategy (see module docstring)."""

    def __init__(
        self,
        config: NyOpenAccumulationBreakoutConfig | None = None,
    ) -> None:
        """Initializes the strategy with a config (defaults match the
        validated backtest settings -- see NyOpenAccumulationBreakoutConfig).
        """
        self.config = config or NyOpenAccumulationBreakoutConfig()
        self.diagnostics = StrategyDiagnostics()
        self._daily_context: DailyContext | None = None
        self._reset_day_state()
        self._current_date: date | None = None

    def set_daily_context(self, context: DailyContext) -> None:
        """Pushes this NY day's bias/liquidity inputs (see DailyContext).

        Must be called by the live-loop caller before the first evaluate()
        of `context.for_date`. Calling it again mid-day (e.g. a corrected
        re-fetch) is safe -- it simply replaces the stored context; already
        -built accumulation/breakout state for today is NOT reset by this
        (only a NY-calendar-date change resets that, in evaluate()).
        """
        self._daily_context = context

    def _reset_day_state(self) -> None:
        self._accum_bars: list[Bar] = []
        self._accum_high: float | None = None
        self._accum_low: float | None = None
        self._accum_ready = False
        self._bars_since_accum_ready = 0
        self._breakout_bar: Bar | None = None
        self._breakout_direction: SignalDirection | None = None
        self._bars_since_breakout = 0
        self._trade_taken = False
        self._day_dead = False  # permanently rejected today (no re-eval work needed, still cheap to keep checking)

    def reset(self) -> None:
        """Resets diagnostics and all day-scoped state (fresh backtest/live run)."""
        self.diagnostics.reset()
        self._reset_day_state()
        self._current_date = None
        self._daily_context = None

    def recommended_max_holding_bars(self, timeframe: Timeframe) -> int | None:
        """No session-derived holding limit is recommended (TP/SL-only exit, like the backtest)."""
        return None

    def _reject(self, reason: RejectionReason) -> None:
        self.diagnostics.record_rejection(reason)
        return None

    def _try_accumulation(self) -> None:
        """Re-derives the accumulation window from self._accum_bars (the
        session's bars so far), mirroring
        scripts/accumulation_breakout_backtest.py's find_accumulation() --
        cheap to rerun each tick since the buffer is small (<= a session).
        """
        baseline = self._daily_context.recent_avg_range
        bars = self._accum_bars
        cfg = self.config
        for start in range(0, max(0, len(bars) - cfg.min_accum_candles + 1)):
            window = bars[start : start + cfg.min_accum_candles]
            if len(window) < cfg.min_accum_candles:
                break
            grp_high = max(b.high for b in window)
            grp_low = min(b.low for b in window)
            span = grp_high - grp_low
            max_body = max(abs(b.close - b.open) for b in window)
            if span > cfg.compression_multiplier * baseline or (span > 0 and max_body > cfg.max_body_fraction * span):
                continue
            end = start + cfg.min_accum_candles
            while end < len(bars) and (end - start) < cfg.max_accum_candles:
                candidate = bars[start : end + 1]
                c_high = max(b.high for b in candidate)
                c_low = min(b.low for b in candidate)
                c_span = c_high - c_low
                c_max_body = max(abs(b.close - b.open) for b in candidate)
                if c_span <= cfg.compression_multiplier * baseline and (c_span == 0 or c_max_body <= cfg.max_body_fraction * c_span):
                    grp_high, grp_low = c_high, c_low
                    end += 1
                else:
                    break
            self._accum_high, self._accum_low = grp_high, grp_low
            self._accum_ready = True
            return

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates the latest M1 bar against the day-scoped accumulation
        -> engulf breakout -> two-level retest state machine. See the
        module docstring for why bias/liquidity context must be pushed in
        separately via set_daily_context() before this is called.
        """
        self.diagnostics.record_evaluation()
        cfg = self.config

        latest_bar = market_state.get_latest_bar()
        if latest_bar is None:
            return self._reject(RejectionReason.NO_LATEST_BAR)

        local_dt = latest_bar.timestamp.astimezone(NY)
        local_date = local_dt.date()
        local_time = local_dt.time()

        if local_date != self._current_date:
            self._reset_day_state()
            self._current_date = local_date

        if self._daily_context is None or self._daily_context.for_date != local_date:
            return self._reject(RejectionReason.NO_TREND)
        bias_str = self._daily_context.bias
        direction = SignalDirection.BUY if bias_str == "LONG" else SignalDirection.SELL

        if self._trade_taken:
            return self._reject(RejectionReason.TRADE_ALREADY_TAKEN)

        if local_time < cfg.window_start:
            return self._reject(RejectionReason.NOT_IN_SESSION)

        # --- STEP 2: accumulation ---
        if not self._accum_ready:
            if local_time < cfg.window_end:
                self._accum_bars.append(latest_bar)
                self._try_accumulation()
            if not self._accum_ready:
                return self._reject(RejectionReason.ACCUMULATION_NOT_READY)

        # --- STEP 3: engulf breakout ---
        if self._breakout_bar is None:
            self._bars_since_accum_ready += 1
            if self._bars_since_accum_ready > cfg.breakout_search_candles:
                return self._reject(RejectionReason.NO_BREAKOUT)

            recent_bars = market_state.bars_view()
            if len(recent_bars) < 2:
                return self._reject(RejectionReason.NO_BREAKOUT)
            prev_bar = recent_bars[-2]
            if _is_engulfing(prev_bar, latest_bar):
                is_short_break = latest_bar.close < self._accum_low
                is_long_break = latest_bar.close > self._accum_high
                if direction == SignalDirection.SELL and is_short_break:
                    self._breakout_bar = latest_bar
                elif direction == SignalDirection.BUY and is_long_break:
                    self._breakout_bar = latest_bar
                # An opposite-direction engulf breakout is intentionally NOT
                # traded and does NOT lock in _breakout_bar -- scanning
                # continues for a same-direction one within the remaining
                # breakout_search_candles budget (matches the backtest).
            if self._breakout_bar is None:
                return self._reject(RejectionReason.NO_BREAKOUT)

        # --- STEP 4: retest levels A/B and entry ---
        self._bars_since_breakout += 1
        if self._bars_since_breakout > cfg.retest_window_candles:
            return self._reject(RejectionReason.NO_RETEST)

        level_a = self._accum_low if direction == SignalDirection.SELL else self._accum_high
        breakout_bar = self._breakout_bar
        b_low = b_high = (breakout_bar.open + breakout_bar.close) / 2.0  # 50%-of-body fallback (see backtest)

        if direction == SignalDirection.SELL:
            touched_a = latest_bar.high >= level_a
            touched_b = latest_bar.high >= b_low
        else:
            touched_a = latest_bar.low <= level_a
            touched_b = latest_bar.low <= b_high

        if not touched_a and not touched_b:
            return self._reject(RejectionReason.NO_RETEST)

        if direction == SignalDirection.SELL:
            entry, entry_level = (level_a, "A") if (not touched_b or level_a <= b_low) else (b_low, "B")
        else:
            entry, entry_level = (level_a, "A") if (not touched_b or level_a >= b_high) else (b_high, "B")

        # --- STEP 5: SL / TP ---
        wick_extreme = breakout_bar.high if direction == SignalDirection.SELL else breakout_bar.low
        if direction == SignalDirection.SELL:
            opp_boundary = max(self._accum_high, wick_extreme)
            sl = opp_boundary + cfg.sl_buffer_points
        else:
            opp_boundary = min(self._accum_low, wick_extreme)
            sl = opp_boundary - cfg.sl_buffer_points
        risk_dist = abs(entry - sl)
        if risk_dist <= 0.0:
            return self._reject(RejectionReason.NON_POSITIVE_RISK)

        ctx = self._daily_context
        candidates: list[tuple[float, str]] = []
        if direction == SignalDirection.SELL:
            for price, label in [(ctx.pdl, "PDL"), (ctx.asia_low, "Asia_low"), (ctx.london_low, "London_low"), (ctx.nearest_swing_low, "swing_low")]:
                if price is not None and price < entry:
                    candidates.append((price, label))
        else:
            for price, label in [(ctx.pdh, "PDH"), (ctx.asia_high, "Asia_high"), (ctx.london_high, "London_high"), (ctx.nearest_swing_high, "swing_high")]:
                if price is not None and price > entry:
                    candidates.append((price, label))

        if not candidates:
            return self._reject(RejectionReason.NO_MATCHING_FVG)  # reused: "no liquidity target found"

        if direction == SignalDirection.SELL:
            tp, target_type = max(candidates, key=lambda c: c[0])
        else:
            tp, target_type = min(candidates, key=lambda c: c[0])

        reward_dist = abs(tp - entry)
        rr = reward_dist / risk_dist
        if rr < cfg.min_rr:
            return self._reject(RejectionReason.RR_GATE_FAILED)
        if cfg.max_rr_cap is not None and rr > cfg.max_rr_cap:
            tp = entry - cfg.max_rr_cap * risk_dist if direction == SignalDirection.SELL else entry + cfg.max_rr_cap * risk_dist
            target_type = f"{target_type}_capped_{cfg.max_rr_cap:g}R"
            rr = cfg.max_rr_cap

        self._trade_taken = True

        direction_label = "Bullish" if direction == SignalDirection.BUY else "Bearish"
        unique_id = uuid.uuid4().hex[:8]
        ts_str = latest_bar.timestamp.strftime("%Y%m%d_%H%M%S_%f")
        setup_id = (
            f"setup_ny_accum_breakout_{market_state.symbol}_{market_state.timeframe.value}_"
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
                f"Daily bias: {bias_str} (HTF structure)",
                f"Accumulation range [{self._accum_low:.2f}, {self._accum_high:.2f}]",
                f"{direction_label} engulfing breakout confirmed",
                f"Retest level {entry_level} @ {entry:.2f}",
                f"TP: {target_type} (RR {rr:.2f})",
            ],
            trigger_reason=(
                f"{direction_label} accumulation breakout-retest: entered at level {entry_level} "
                f"({entry:.2f}), targeting {target_type} ({tp:.2f})"
            ),
            invalidations=[
                "Price closes back inside the accumulation range",
                "Price breaches Stop Loss zone",
            ],
            related_structure_break=None,
            related_order_block=None,
            related_fvg=None,
            timestamp=latest_bar.timestamp,
            strategy_name=self.__class__.__name__,
        )
