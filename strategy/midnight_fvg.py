"""MidnightFvgStrategy: live port of the "Midnight FVG" strategy validated in
scripts/first_fvg_backtest.py (SESSIONS=[midnight], USE_BIAS_FILTER=False,
REQUIRE_DISPLACEMENT=False, FIXED_TP_R=2.5, MIN_GAP_POINTS=3.0, ENTRY_MODE=
"touch") -- see BACKTEST_FINDINGS.md section 2.3 for the full derivation
(409 trades over ~4.1yr, WR 34.5%, PF 1.30) and MIDNIGHT_FVG_BOT_SPEC.md for
the task this class implements.

Unlike NyOpenAccumulationBreakoutStrategy (strategy/ny_open_accumulation_breakout.py),
this strategy needs NO cross-timeframe DailyContext: there's no bias filter
and no liquidity-hunt TP (a fixed R multiple replaces it), so the M1-only
MarketState this class's evaluate() receives is sufficient on its own. All
state is scoped to one NY calendar day and reset when the date changes --
the same day-state-machine pattern as NyOpenAccumulationBreakoutStrategy,
just without the daily-bias step.

Detection re-derives the first FVG from a small (<= session length + 2 bars)
growing buffer on every tick -- cheap to rerun each tick, same approach as
NyOpenAccumulationBreakoutStrategy._try_accumulation(). Uses this repo's own
smc/fvg.py (FVGDetector) [and smc/displacement.py (DisplacementDetector)
only if require_displacement=True, off by default -- see
MidnightFvgConfig.require_displacement docstring for why that toggle isn't
fully faithful to the batch script in the live class] rather than
reimplementing gap detection.

Cross-midnight FVG edge case: a 3-candle FVG whose MIDDLE candle sits right
at the session's opening bar (00:00) needs its FIRST candle (23:59) from the
PREVIOUS calendar day to be detectable -- scripts/first_fvg_backtest.py
handles this by prepending a tail of the previous day's bars
(`context_before`) to its per-day window. This class reproduces that with a
tiny (maxlen=2) rolling `_trailing_tail` buffer updated every tick
regardless of session, seeded into the new day's detection buffer on
rollover -- O(1) bookkeeping, no full-history rescan.

Entry: direct touch of the FVG's near edge (default entry_mode="touch", no
confirmation candle) or "confirmation" (tag the zone AND close back outside
it), tracked bar-by-bar against every subsequent bar until the day rolls
over (retest_window_candles=None, matching the ALREADY-VALIDATED batch
script's actual behavior -- see the field's own docstring for an important
discrepancy against MIDNIGHT_FVG_BOT_SPEC.md's prose).

SL: the low (bullish) / high (bearish) of the CANDLE THAT CREATED the FVG
(the middle/displacement candle) -- NOT the FVG zone's own boundary. See
BACKTEST_FINDINGS.md step 4 for why this out-performed the FVG-edge+buffer
SL rule used in an earlier iteration.

TP: fixed R multiple (entry +/- fixed_tp_r * risk_dist, default 2.5R). No
liquidity-hunt fallback -- this strategy doesn't compute PDH/PDL/swings at
all, unlike NyOpenAccumulationBreakoutStrategy.

Exit management (TP/SL fill, and the rare EOD-flat-close backstop -- 1/409
trades in the batch backtest) is NOT this class's responsibility, same as
every other strategy in this codebase: TradeManager/the broker watches the
open position's stop_loss/take_profit (see run_live_midnight_fvg.py's
run_once()/_manage_open_trade()). This class only ever proposes a
TradeSetup candidate; it never manages or closes a position, and -- like
NyOpenAccumulationBreakoutStrategy -- does NOT implement the EOD-flat-close
backstop live. Flagged as a known gap: paper-test through several session
rollovers before trusting this in practice (see MIDNIGHT_FVG_BOT_SPEC.md
section 5, safety requirements).

Safety: this class only ever RETURNS a TradeSetup candidate; it never
places an order -- see run_live_midnight_fvg.py / run_live_demo.py.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from core.models import Bar, SignalDirection, Timeframe
from core.validation import require_positive
from market_structure.structure_models import MarketState
from smc.displacement import DisplacementDetector
from smc.fvg import FVGDetector, FVGDirection
from strategy.diagnostics import RejectionReason, StrategyDiagnostics
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MidnightFvgConfig:
    """Configuration for MidnightFvgStrategy. Defaults match the validated
    scripts/first_fvg_backtest.py config -- see module docstring.

    Attributes:
        session_start, session_end: The FVG-detection window, NY local time
            (default 00:00-00:30). Only the FVG's middle/displacement
            candle needs to fall in this window (matches
            find_first_fvg()'s `window_start <= fvg.timestamp.time() <
            window_end` check) -- the retest/entry search is NOT bounded to
            this window, see retest_window_candles below.
        min_gap_points: Minimum FVG gap size, in price points (wick-to-wick,
            not body-to-body -- see FVGDetector). Default 3.0, the
            min-gap-sweep winner (BACKTEST_FINDINGS.md step 6).
        entry_mode: "touch" (fill at the FVG's near edge on first touch,
            the validated default) or "confirmation" (wait for a bar to tag
            the zone AND close back outside it in the trade direction, fill
            at that close -- backtested weaker, kept only for parity with
            scripts/first_fvg_backtest.py's toggle).
        require_displacement: If True, only count an FVG whose middle
            candle is also a DisplacementDetector hit (ATR-multiple
            expansion). Default False (the validated config -- displacement
            filtering cut trade count ~2.4x for a worse PF, see
            BACKTEST_FINDINGS.md step 2). NOTE: unlike the batch script,
            this live class's ATR-14 warmup only has this session's own
            growing buffer (plus the 2-bar cross-midnight tail) to work
            with -- nowhere near ATR_PERIOD+1 bars in a 30-minute window --
            so setting this True will effectively never fire live. Left in
            purely for config-shape parity with the batch script; do not
            rely on it.
        atr_multiplier, atr_period: DisplacementDetector params, only
            consulted when require_displacement=True (see above caveat).
        fixed_tp_r: Take-profit as a multiple of the SL risk distance.
            Default 2.5 (the R-sweep winner, BACKTEST_FINDINGS.md step 5).
        retest_window_candles: Caps how many bars after the FVG forms the
            retest/entry search runs before the setup is abandoned for the
            day. Default None = uncapped (effectively "rest of the trading
            day", since state resets at the next NY calendar-day rollover
            regardless) -- this is what scripts/first_fvg_backtest.py's
            process_session() ACTUALLY does (`rest_of_day = [b for b in
            day_bars if b.ts > fvg_end_ts]`, no bar-count cap), and is what
            produced the validated 409-trade/PF-1.30 result this class
            ports. MIDNIGHT_FVG_BOT_SPEC.md section 1 describes a 5-candle
            retest cutoff ("Retest 5 şam ərzində baş verməzsə, o günkü
            setup ləğv olunur") -- that is NOT what the already-validated
            script does, so the default here intentionally matches the
            validated CODE over the spec's prose. Set an int (e.g. 5) to
            opt into that stricter behavior instead; scripts/
            replay_live_strategy_check_midnight_fvg.py compares both.
    """

    session_start: time = time(0, 0)
    session_end: time = time(0, 30)
    min_gap_points: float = 3.0
    entry_mode: str = "touch"
    require_displacement: bool = False
    atr_multiplier: float = 2.0
    atr_period: int = 14
    fixed_tp_r: float = 2.5
    retest_window_candles: int | None = None

    def __post_init__(self) -> None:
        """Validates parameter ranges.

        Raises:
            ValueError: If any *_points/*_r/atr_* field is not strictly
                positive, entry_mode is not "touch"/"confirmation", or
                session_start is not strictly before session_end.
        """
        require_positive(self.min_gap_points, "min_gap_points")
        require_positive(self.fixed_tp_r, "fixed_tp_r")
        require_positive(self.atr_multiplier, "atr_multiplier")
        require_positive(self.atr_period, "atr_period")
        if self.retest_window_candles is not None:
            require_positive(self.retest_window_candles, "retest_window_candles")
        if self.entry_mode not in ("touch", "confirmation"):
            raise ValueError(f"entry_mode must be 'touch' or 'confirmation', got {self.entry_mode!r}")
        if self.session_start >= self.session_end:
            raise ValueError(
                f"session_start ({self.session_start}) must be strictly before "
                f"session_end ({self.session_end})."
            )


class MidnightFvgStrategy(TradeSetupStrategy):
    """Live port of the Midnight FVG strategy (see module docstring)."""

    def __init__(self, config: MidnightFvgConfig | None = None) -> None:
        """Initializes the strategy with a config (defaults match the
        validated backtest settings -- see MidnightFvgConfig).
        """
        self.config = config or MidnightFvgConfig()
        self.diagnostics = StrategyDiagnostics()
        self._current_date: date | None = None
        # Rolling all-time (not day-scoped) tail of the last 2 bars seen --
        # NOT reset by _reset_day_state(), see module docstring's
        # "Cross-midnight FVG edge case".
        self._trailing_tail: deque[Bar] = deque(maxlen=2)
        self._reset_day_state()

    def _reset_day_state(self) -> None:
        # Seed with yesterday's trailing tail ONLY when the session starts
        # exactly at local midnight (matches scripts/first_fvg_backtest.py's
        # `if window_start == time(0, 0)` guard) -- for any other
        # session_start, a prior-day tail is not a real adjacency concern.
        # Further restricted to bars within 5 minutes of midnight (mirrors
        # that script's own `ts.time() >= time(22, 0)` context_before filter,
        # just tighter): with continuous data the tail is always exactly
        # 23:58/23:59, so this never excludes a real case, but it guards
        # against seeding stale/unrelated bars after a data gap whose
        # time-of-day coincidentally falls inside [session_start,
        # session_end) and would otherwise be mistaken for real session bars
        # (see tests/test_midnight_fvg.py's day-rollover regression test).
        seed: list[Bar] = []
        if self.config.session_start == time(0, 0):
            cutoff = time(23, 55)
            seed = [b for b in self._trailing_tail if b.timestamp.astimezone(NY).time() >= cutoff]
        self._session_bars: list[Bar] = seed
        self._fvg_found = False
        self._fvg_direction: SignalDirection | None = None
        self._fvg_upper: float | None = None
        self._fvg_lower: float | None = None
        self._fvg_middle_bar: Bar | None = None  # the displacement/middle candle -- its wick is the SL
        self._bars_since_fvg = 0
        self._trade_taken = False

    def reset(self) -> None:
        """Resets diagnostics and all state (fresh backtest/live run)."""
        self.diagnostics.reset()
        self._current_date = None
        self._trailing_tail.clear()
        self._reset_day_state()

    def recommended_max_holding_bars(self, timeframe: Timeframe) -> int | None:
        """No session-derived holding limit is recommended (TP/SL-only exit, like the backtest)."""
        return None

    def _reject(self, reason: RejectionReason) -> None:
        self.diagnostics.record_rejection(reason)
        return None

    def _try_find_fvg(self) -> None:
        """Re-derives the first FVG from self._session_bars (this session's
        bars so far, plus up to 2 cross-midnight tail bars -- see module
        docstring), mirroring scripts/first_fvg_backtest.py's
        find_first_fvg(). Cheap to rerun each tick: the buffer is capped at
        one session's worth of bars (<=30 M1 bars for the default
        00:00-00:30 window) plus the 2-bar tail.
        """
        cfg = self.config
        bars = self._session_bars
        if len(bars) < 3:
            return

        displaced: set[datetime] | None = None
        if cfg.require_displacement:
            if len(bars) < cfg.atr_period + 1:
                return  # not enough warmup for ATR yet -- see require_displacement's docstring caveat
            disp = DisplacementDetector(atr_multiplier=cfg.atr_multiplier, atr_period=cfg.atr_period)
            displaced = {d.timestamp for d in disp.find_displacements(bars)}

        fvg_detector = FVGDetector(min_gap_pips=cfg.min_gap_points, pip_size=1.0)
        fvgs = fvg_detector.detect_fvgs(bars)
        candidates = [
            fvg
            for fvg in fvgs
            if cfg.session_start <= fvg.timestamp.astimezone(NY).time() < cfg.session_end
            and (displaced is None or fvg.timestamp in displaced)
        ]
        if not candidates:
            return
        candidates.sort(key=lambda f: f.timestamp)
        first = candidates[0]
        self._fvg_found = True
        self._fvg_direction = SignalDirection.BUY if first.direction == FVGDirection.BULLISH else SignalDirection.SELL
        self._fvg_upper = first.upper_price
        self._fvg_lower = first.lower_price
        self._fvg_middle_bar = bars[first.start_index + 1]

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates the latest M1 bar against the day-scoped FVG-detect ->
        retest-touch state machine. See the module docstring for why no
        cross-timeframe context is needed (unlike
        NyOpenAccumulationBreakoutStrategy).
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

        try:
            if self._trade_taken:
                return self._reject(RejectionReason.TRADE_ALREADY_TAKEN)

            # --- STEP 1: FVG detection, within the session window only ---
            if not self._fvg_found:
                if local_time < cfg.session_start:
                    return self._reject(RejectionReason.NOT_IN_SESSION)
                if local_time < cfg.session_end:
                    self._session_bars.append(latest_bar)
                    self._try_find_fvg()
                    if self._fvg_found:
                        # Just formed on THIS tick (latest_bar is the FVG's own
                        # end/third candle) -- scripts/first_fvg_backtest.py's
                        # retest search is `[b for b in day_bars if b.ts >
                        # fvg_end_ts]`, STRICTLY bars AFTER the end candle, so
                        # the end candle itself must never also be checked as a
                        # retest candidate here (for a fresh gap, the end
                        # candle's own wick trivially equals the FVG's near
                        # edge, which would otherwise fire an instant false
                        # "retest" the moment the gap forms).
                        return self._reject(RejectionReason.NO_RETEST)
                if not self._fvg_found:
                    return self._reject(
                        RejectionReason.NO_DISPLACEMENT if cfg.require_displacement else RejectionReason.NO_MATCHING_FVG
                    )

            # --- STEP 2: retest / entry (see retest_window_candles docstring) ---
            self._bars_since_fvg += 1
            if cfg.retest_window_candles is not None and self._bars_since_fvg > cfg.retest_window_candles:
                return self._reject(RejectionReason.NO_RETEST)

            direction = self._fvg_direction
            upper, lower = self._fvg_upper, self._fvg_lower
            entry: float | None = None
            if cfg.entry_mode == "touch":
                if direction == SignalDirection.BUY and latest_bar.low <= upper:
                    entry = upper
                elif direction == SignalDirection.SELL and latest_bar.high >= lower:
                    entry = lower
            else:  # "confirmation"
                if direction == SignalDirection.BUY and latest_bar.low <= upper and latest_bar.close > upper:
                    entry = latest_bar.close
                elif direction == SignalDirection.SELL and latest_bar.high >= lower and latest_bar.close < lower:
                    entry = latest_bar.close

            if entry is None:
                return self._reject(RejectionReason.NO_RETEST)

            # --- STEP 3: SL = the creating (middle/displacement) candle's own wick ---
            middle = self._fvg_middle_bar
            assert middle is not None
            sl = middle.low if direction == SignalDirection.BUY else middle.high
            risk_dist = abs(entry - sl)
            if risk_dist <= 0.0:
                return self._reject(RejectionReason.NON_POSITIVE_RISK)

            # --- STEP 4: fixed-R take-profit ---
            tp = entry + cfg.fixed_tp_r * risk_dist if direction == SignalDirection.BUY else entry - cfg.fixed_tp_r * risk_dist

            self._trade_taken = True

            direction_label = "Bullish" if direction == SignalDirection.BUY else "Bearish"
            unique_id = uuid.uuid4().hex[:8]
            ts_str = latest_bar.timestamp.strftime("%Y%m%d_%H%M%S_%f")
            setup_id = (
                f"setup_midnight_fvg_{market_state.symbol}_{market_state.timeframe.value}_"
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
                    f"{direction_label} FVG in {cfg.session_start}-{cfg.session_end} NY session "
                    f"(gap >= {cfg.min_gap_points:g}pt)",
                    f"Entry: {cfg.entry_mode} @ {entry:.2f}",
                    f"SL: displacement candle wick @ {sl:.2f}",
                    f"TP: fixed {cfg.fixed_tp_r:g}R @ {tp:.2f}",
                ],
                trigger_reason=(
                    f"{direction_label} Midnight FVG retest: entered at {entry:.2f}, "
                    f"SL {sl:.2f}, fixed {cfg.fixed_tp_r:g}R TP {tp:.2f}"
                ),
                invalidations=[
                    "Price closes back through the FVG's far edge before touching the near edge",
                    "Price breaches Stop Loss zone",
                ],
                related_structure_break=None,
                related_order_block=None,
                related_fvg=None,
                timestamp=latest_bar.timestamp,
                strategy_name=self.__class__.__name__,
            )
        finally:
            self._trailing_tail.append(latest_bar)
