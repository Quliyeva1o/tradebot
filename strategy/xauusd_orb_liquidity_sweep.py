"""XauusdOrbLiquiditySweepStrategy: live port of Setup B ("liquidity sweep +
displacement + FVG retest") from scripts/xauusd_orb_liquidity_sweep_backtest.py.

Deliberately Setup B ONLY -- Setup A (OR breakout + retest) was validated
alongside it and found net-losing after spread on BOTH backtested symbols
(XAUUSD: PF 0.81 over 2y; NAS100: PF 0.75 over 4y, -40.94R), so it was never
a candidate for a live port. Porting it anyway "for completeness" would ship
a strategy this session's own validation already rejected.

Setup B's own validation (2026-08-31 session, XAUUSD.ifx M5, 2022-12-2026-08).
IMPORTANT: use the SETUP-B-ISOLATED numbers below (`--enable-breakout` off
in the batch script), not any earlier n=101/PF 1.62 figure quoted mid-session
-- that number came from filtering a COMBINED Setup-A+B run to
setup_type=="reversal", where Setup A's own (excluded, losing) trades were
still occupying the shared position slot and suppressing genuine Setup-B
opportunities. This class never runs Setup A, so the isolated run is the
correct baseline; the live-class fidelity check below (n=115 vs n=115,
identical trade dates/directions) only reached an exact match once compared
against it -- see scripts/backtest_xauusd_orb_live_class.py's own docstring:
  - Isolated batch (spread-adjusted): n=115, PF 1.576, WR 47.8%, +36.34R
  - Live class (same data, same spread): n=115, PF 1.680, WR 48.7%, +43.51R
    (the two PF/R figures differ slightly -- same 115 trades on the same
    days -- from the batch script's EOD force-close vs. this harness
    letting each trade run to its real SL/TP; see that script's docstring)
  - Bootstrap (isolated n=115, gross): 99.8% probability of a real edge
    (PF median 1.78, 90% CI [1.30, 2.44]) -- matches the earlier (flawed
    baseline) measurement almost exactly, so the combined-run filtering
    bug did not meaningfully change this particular conclusion, only the
    trade count/PF point estimate.
  - Recency split (80/20, gross): PF 1.96 (first 80%) -> 1.24 (last 20%,
    n=23) -- some decay, still solidly >1.
  - Walk-forward (isolated, 7 six-month folds, net of spread): 7/7 PF>=1.0,
    every fold positive including the most recent (PF 1.085..3.091 range).
  - Monte Carlo (isolated, real 0.5% risk, fixed-fractional): expected
    +9.7% over the full window, worst-case drawdown 18.8%, 0% risk of ruin.
  - Regime-conditioned (isolated, gross): positive in ALL THREE regimes
    (TRENDING PF 1.656 n=18, MEAN_REVERTING PF 2.538 n=22, RANGING PF 1.650
    n=75) -- unlike FirstFvg15mStrategy/SrDailyBiasStrategy, whose entire
    edge is RANGING-only, so no regime gate is offered here; adding one
    would filter out a genuinely regime-independent edge, not protect it.
  - Spread breakeven ~2.0pt vs the 0.39pt actually charged -- large margin.
Cross-checked against NAS100 (USTEC, 2022-07-2026-08, n=102, from
combined-run filtering -- NOT re-isolated after the bug fix above, treat as
a rougher corroborating signal than the XAUUSD numbers): PF 1.69, bootstrap
99.9%, but only 7/8 folds passed (most recent 6-month fold failed, PF
0.887) and the edge there IS RANGING-concentrated (PF 2.87 RANGING vs 0.86
MEAN_REVERTING) -- this class targets XAUUSD specifically; the NAS100
result is a corroborating signal, not a second validated deployment target.
THE ABOVE M5 NUMBERS ARE HISTORICAL -- this class no longer runs on M5, see
the M15 section immediately below, which superseded it.

M15 port (2026-09-01 session): the prior session's own top TODO was to move
this class from the M5 spec-literal OR to the M15 range that its own
research (batch script `--bar-minutes 15 --entry-window-end 11:00`) found
outperforms M5 in every fill-mode/window comparison it ran. Done this
session: `entry_window_end` default changed 10:00 -> 11:00 (OR candle is
therefore 09:30-09:45, not 09:30-09:35; see Mechanics below). Re-validated
on a DIFFERENT machine/account than the M5 numbers above -- this one runs
FXTM-Demo02 (login 67660753), where gold is plain "XAUUSD" (not
"XAUUSD.ifx"), using this account's own `data/history/XAUUSD_M1.csv`
(2020-01-02 -> 2026-08-27, 6.7 years, wider and from a different broker feed
than the M5 session's 3.75y XAUUSD.ifx file) -- a genuinely independent
out-of-sample/out-of-broker check, not a re-run of the same numbers:
  - Isolated batch, idealized zone-edge fill (spread-adjusted 0.39pt, 0.5%
    risk): n=187, WR 59.9%, PF 2.218, net R +76.56, PnL +$38,280.
  - Isolated batch, REALISTIC next-bar-open fill (matches how
    execution/fill_simulator.py actually fills a live MARKET order -- see
    the M5-era finding below that zone-edge overstates results repo-wide):
    n=137, WR 51.1%, PF 1.326, net R +17.77, PnL +$8,886. This is the
    number to trust for what live/paper trading should actually produce;
    the zone-edge figure above is a ceiling, not an expectation.
  - Live-class fidelity (this module's actual class, bar-by-bar, zone-edge
    mode to match how this class fills -- see scripts/backtest_xauusd_orb_live_class.py):
    n=181 vs batch's 187, WR 49.2% vs 59.9%, PF (net) 1.768 vs 2.218, total
    net R 74.94 vs 76.56. A 10pp win-rate gap looked alarming enough to
    warrant tracing every one of the 6 missing trades individually (not
    hand-waved as "the known EOD gap") -- all 6 are now root-caused, none
    are a live-class bug:
      * 4/6: the SAME accepted gap already documented for the M5 port --
        this harness's own outcome simulation has no EOD force-close, so a
        trade opened days earlier can still be "open" (not yet at its real
        SL/TP) when a later setup fires, and the harness (correctly, like
        a real broker with one open position) skips the later setup. M15's
        wider OR-derived stops make trades take longer to resolve than
        M5's did, so this harness quirk bites more often here (4/187 vs
        the M5 port's much smaller fraction) -- still not a fidelity bug,
        since a real live TradeManager would make the exact same call (see
        run_live_xauusd_orb.py's `run_once`: no new-trade evaluation while
        a position is open).
      * 1/6 (2026-08-27, the LAST day in the dataset): the fidelity
        harness's `simulate_outcome` ran out of future bars before the
        trade resolved and aborted the whole replay (`if not resolved:
        break`) -- an artifact of finite backtest data ending mid-trade,
        not a real divergence; live trading wouldn't hit this at all.
      * 1/6 (2022-12-09): a genuine, tiny batch-script edge case, not a
        live-class miss. Price kept falling after the sweep bar, so the
        FVG zone's near edge (the entry) ended up BELOW the original sweep
        wick (the stop anchor) -- an inverted SL sitting on the wrong side
        of entry. `scripts/xauusd_orb_liquidity_sweep_backtest.py`'s
        `open_trade()` computes `risk = abs(entry_price - sl)` (unsigned),
        so it silently accepted this as a normal small-risk trade (which
        happened to hit TP). This class computes `risk = zone_top - sl`
        (signed) and correctly rejects it via `risk <= 0`
        (RISK_OUT_OF_BOUNDS) -- the live class's behavior here is more
        correct than the batch script's, not less. Affects roughly 1/187
        trades in this dataset; not fixed in the batch script this session
        (would touch its own already-cited historical numbers) -- flagged
        as a real, low-priority correctness follow-up, not blocking.
  - Also answers the prior session's open §2.8/§3 question ("is the real
    bottleneck sweep rarity or the 4-bar REVERSAL_LOOKBACK_BARS lookback?")
    definitively: neither batch nor this class EVER hits the lookback-expiry
    branch across the full 6.7-year dataset (verified by instrumenting it).
    The entry window is exactly 5 bars wide (matching the lookback's own
    4-bar-after-sweep budget) in BOTH the M5 and M15 configs, so the window
    itself always ends a sweep's candidacy before the lookback ever could --
    `reversal_lookback_bars` is currently fully redundant with
    `entry_window_end`, not a separate constraint. Changing it would do
    nothing at the current window width; narrowing the window is the lever
    that would actually matter, and this session did not test that.
  - Full battery run 2026-09-01 (same session, via
    scripts/xauusd_orb_validation.py -- this script had its OWN unfixed copy
    of the A+B-combined-filtering bug described above, plus M5 defaults;
    both fixed as part of this run). Realistic next-open, isolated Setup B,
    net of 0.39pt spread, n=137, 2020-01 -> 2026-08 (this account's data):
      * Full history: WR 51.1%, PF 1.33, net R +17.8.
      * Bootstrap (5000x): 97.7% probability of a real (PF>1) edge, median
        PF 1.50, 90% CI [1.08, 2.05].
      * Recency split (80/20): PF 1.46 (first 80%) -> 1.63 (last 20%) --
        improving, not decaying.
      * Walk-forward (7 ~1-year calendar folds): 5/7 PF>=1.0. BOTH failing
        folds are 2020-2021 (PF 0.34, 0.59, net -7.3R/-4.5R combined) --
        every fold from late-2021 onward passes (PF 1.13-8.49, one of which
        rests on only n=15 and should be read as noise, not a real 8x
        edge). Read as: this configuration was NOT profitable in its
        earliest ~2 years of data and has been consistently profitable
        since -- not evenly good across the whole window.
      * Monte Carlo, REAL sizing (0.5% fixed-fractional, 5000 trials,
        bootstrap + adverse noise): expected return only +1.7% over the
        FULL 6.7-year window (median final balance $101,447, 90% CI
        [$89,535, $115,263]), median drawdown 6.1%, worst-case 22.3%, 0%
        risk of ruin. This is the honest headline, not the full-history
        PF/R above -- +1.7% total over 6.7 years is a thin, barely-positive
        edge once realistic execution noise is modeled, materially weaker
        than "PF 1.33, +$17,800" sounds standalone. (A fixed-$-per-trade
        sizing model, which does NOT match how the live bots actually
        size, shows a more flattering +3.4%/47.3% worst-drawdown --
        not the number to use.)
      * Regime-conditioned (gross): positive in all three trend regimes,
        but NOT evenly -- TRENDING PF 13.05 (n=12, too small to trust the
        point estimate), MEAN_REVERTING PF 1.82 (n=40), RANGING PF 1.10
        (n=84, the MAJORITY of trades, and the thinnest margin of the
        three). Unlike First FVG/SR+Bias (edge is RANGING-only), this one
        does NOT depend on one regime -- but most of its trades come from
        the regime with the weakest edge, which tempers the "regime-
        independent" framing somewhat.
    Net read: a real, statistically-supported edge (97.7% bootstrap, 5/7
    walk-forward folds, improving recency split) that is modest in size at
    real risk (+1.7% Monte Carlo expectation over 6.7 years) and was not
    profitable in its own earliest ~2 years of data -- weaker than First
    FVG/SR+Bias's own battery results, not a slam-dunk case for live
    capital at anything beyond the already-conservative 0.5% default.

NOT YET forward/paper-validated -- this is a fresh backtest-to-live port,
not a strategy with a live track record like FirstFvg15mStrategy/
SrDailyBiasStrategy (both already fidelity-checked against months of real
order flow). Route through a Paper runner first, the same "validate before
real order routing" sequence already applied to the RANGING-regime gate on
those two classes (see ADVANCED_VALIDATION_REPORT.md #6). Also note:
run_live_xauusd_orb.py's `--symbol` default is now "XAUUSD" (this account's
ticker) and `--timeframe` default is now "M15" -- verify both against
`mt5.symbols_get()` before running on any OTHER account/broker.

Mechanics (M15 bars, NY session -- see the batch script's own docstring for
the full spec derivation; this was M5/09:35-10:00 before the 2026-09-01 M15
port, see the module docstring's M15 section above):
  1. Opening Range = the single 09:30-09:45 NY candle's high/low.
  2. Entry window: 09:45-11:00 NY. No new setup may start outside it (a
     sweep detected right at 10:45 that doesn't complete its FVG+retest
     before 11:00 simply expires unfilled -- this mirrors the validated
     batch script's own behavior exactly: the reversal state machine is
     gated behind the SAME in-window check as new-sweep detection, so a
     pending sweep gets no extra time past 11:00 either). The window is
     exactly 5 M15 bars wide, same as `reversal_lookback_bars`'s 4-bars-
     after-sweep budget -- in practice the window always ends a pending
     sweep's candidacy before the lookback itself could ever expire it
     (verified: the lookback-expiry branch never fires across 6.7 years of
     data), so `reversal_lookback_bars` is currently redundant with this
     window width, not an independent filter.
  3. BUY reversal: price wicks below OR Low and closes back above it
     (sweep), then within `reversal_lookback_bars` a same-direction 3-candle
     FVG forms (ATR-scaled min gap) whose middle candle itself qualifies as
     a displacement candle -- entry at the FVG-confirming bar's own close
     via its near edge. SL = sweep wick low - an ATR buffer. TP = fixed
     `fixed_tp_r` R. SELL reversal is the exact mirror off OR High.
  4. At most `max_trades_per_day` (default 1 -- see the config's own
     docstring for why this differs from the spec's "2") trades/day; each
     of the two directions (BUY/SELL reversal) fires at most ONCE per day,
     which is what makes "revenge-trading the same level" structurally
     impossible here (a stopped-out reversal on one side can never re-arm
     the same day) -- same guarantee the batch script's `setup_used` dict
     gives. Re-checked on M15 (this session): 0 of 187 validated trades
     ever shared a calendar day, same as the M5 finding -- cap=1 still
     loses no validated edge.

Timeframe assumption: this class assumes it is fed M15 bars specifically
(the Opening Range is defined as exactly one M15 candle) -- same undeclared
TF-coupling as FirstFvg15mStrategy's own M15 assumption; feeding it any
other timeframe silently computes a differently-sized "opening range" that
was never backtested.

Safety: this class only ever RETURNS a TradeSetup candidate; it never places
an order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from zoneinfo import ZoneInfo

from core.models import Bar, SignalDirection, Timeframe
from core.validation import require_positive
from market_structure.structure_models import MarketState
from strategy.diagnostics import RejectionReason, StrategyDiagnostics
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class XauusdOrbLiquiditySweepConfig:
    """Configuration for XauusdOrbLiquiditySweepStrategy. Defaults match the
    validated scripts/xauusd_orb_liquidity_sweep_backtest.py config exactly
    -- see module docstring for the backing numbers.

    Attributes:
        or_start: NY local time the single Opening Range candle begins
            (default 09:30). Assumes M15 bars -- see module docstring. (The
            OR candle is therefore 09:30-09:45, not a M5 09:30-09:35 candle.)
        entry_window_end: NY local time after which no NEW setup may start
            (default 11:00, i.e. 5 M15 bars past the OR -- the same
            bar-count width as the original M5 spec's 09:35-10:00 window,
            per the validated batch script's `--bar-minutes 15
            --entry-window-end 11:00` config, see module docstring §M15).
            A sweep already pending when this passes gets no grace period --
            matches the validated batch script exactly.
        fixed_tp_r: Take-profit as a multiple of the SL risk distance.
            Default 2.0 -- the batch script's own spec-preferred choice
            (section 5: "backtest zamanı qaydanı dəyişməmək üçün əvvəlcə
            sabit 2R test etmək daha düzgündür"); do not silently swap for
            the liquidity-boundary TP mode, which the batch script's
            `--reversal-tp-mode liquidity` run found changed only 1/64
            trades (the OR range is a single 5-min candle, almost always
            narrower than 2R) -- effectively a no-op dressed up as a
            different rule.
        max_trades_per_day: Hard cap on trades opened per NY calendar day.
            Default 1, NOT the spec's "2" -- deliberately conservative. The
            batch script freezes its whole state machine while a simulated
            position is open (a free byproduct of it also simulating fills
            in the same loop); this class, like every other strategy here,
            only ever proposes a TradeSetup and is never told by the engine
            whether it was filled or when it closed, so it cannot reproduce
            that freeze. Checked directly against the validated trade log
            (artifacts/xauusd_orb_reversal_trades_4yr.csv): 0 of 101 trades
            ever shared a calendar day, so a cap of 1 loses no validated
            edge while being provably identical to the batch script (a
            fidelity check at cap=2 found 14 EXTRA trades vs batch's 101 --
            exactly the freeze gap -- before this default was set to 1).
            Raise only alongside a real engine-level "was this setup filled"
            feedback channel, not by itself.
        reversal_lookback_bars: Bars after a sweep within which the
            displacement+FVG must complete, or the sweep expires unfilled.
        displacement_atr_mult: Minimum candle range (as an ATR multiple) for
            a candle to count as "displacement".
        fvg_min_gap_atr: Minimum 3-candle imbalance size, ATR-scaled.
        sl_buffer_atr: Buffer beyond the sweep wick extreme, ATR-scaled.
        max_risk_atr_mult: Reject a setup if its risk distance exceeds this
            many ATRs (spec: "SL həddindən artıq böyükdürsə -> trade
            yoxdur").
        atr_len: Wilder ATR period, computed incrementally bar-by-bar
            (same formula as SrDailyBiasStrategy._update_atr_adx).
    """

    or_start: time = time(9, 30)
    entry_window_end: time = time(11, 0)
    fixed_tp_r: float = 2.0
    max_trades_per_day: int = 1
    reversal_lookback_bars: int = 4
    displacement_atr_mult: float = 1.2
    fvg_min_gap_atr: float = 0.05
    sl_buffer_atr: float = 0.1
    max_risk_atr_mult: float = 3.0
    atr_len: int = 14

    def __post_init__(self) -> None:
        require_positive(self.fixed_tp_r, "fixed_tp_r")
        require_positive(self.max_trades_per_day, "max_trades_per_day")
        require_positive(self.reversal_lookback_bars, "reversal_lookback_bars")
        require_positive(self.displacement_atr_mult, "displacement_atr_mult")
        require_positive(self.fvg_min_gap_atr, "fvg_min_gap_atr")
        require_positive(self.sl_buffer_atr, "sl_buffer_atr")
        require_positive(self.max_risk_atr_mult, "max_risk_atr_mult")
        require_positive(self.atr_len, "atr_len")


def _is_displacement(bar: Bar, atr_val: float, bullish: bool, mult: float) -> bool:
    rng = bar.high - bar.low
    if rng <= 0 or atr_val <= 0 or rng < mult * atr_val:
        return False
    if abs(bar.close - bar.open) < 0.5 * rng:
        return False
    return (bar.close > bar.open) if bullish else (bar.close < bar.open)


class XauusdOrbLiquiditySweepStrategy(TradeSetupStrategy):
    """Live port of Setup B ("liquidity sweep + displacement + FVG retest")
    from the XAUUSD 09:30 ORB spec (see module docstring).
    """

    def __init__(self, config: XauusdOrbLiquiditySweepConfig | None = None) -> None:
        self.config = config or XauusdOrbLiquiditySweepConfig()
        self.diagnostics = StrategyDiagnostics()
        self._current_date: date | None = None
        self._prev_bar: Bar | None = None  # for ATR, continuous across days
        self._atr: float | None = None
        self._reset_day_state()

    def _reset_day_state(self) -> None:
        self._session_bars: list[Bar] = []  # OR bar (index 0) + every bar since, this NY day only
        self._or_captured = False
        self._or_high: float | None = None
        self._or_low: float | None = None
        self._clean_session_open: bool | None = None
        self._trades_today = 0
        self._buy_reversal_used = False
        self._sell_reversal_used = False
        self._sweep_down_idx: int | None = None
        self._sweep_down_low: float | None = None
        self._sweep_up_idx: int | None = None
        self._sweep_up_high: float | None = None

    def reset(self) -> None:
        """Resets diagnostics and all state (fresh backtest/live run)."""
        self.diagnostics.reset()
        self._current_date = None
        self._prev_bar = None
        self._atr = None
        self._reset_day_state()

    def recommended_max_holding_bars(self, timeframe: Timeframe) -> int | None:
        """No holding-bars recommendation -- exit is TP/SL-only, managed by
        TradeManager/broker like every other strategy here.
        """
        return None

    def _reject(self, reason: RejectionReason) -> None:
        self.diagnostics.record_rejection(reason)
        return None

    def _update_atr(self, bar: Bar) -> None:
        if self._prev_bar is None:
            self._prev_bar = bar
            return
        prev = self._prev_bar
        tr = max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close))
        alpha = 1 / self.config.atr_len
        self._atr = tr if self._atr is None else (alpha * tr + (1 - alpha) * self._atr)
        self._prev_bar = bar

    def _build_setup(
        self, market_state: MarketState, direction: SignalDirection, entry: float, stop: float, tp: float,
        signal_time, latest_bar: Bar,
    ) -> TradeSetup:
        direction_label = "Bullish" if direction == SignalDirection.BUY else "Bearish"
        ts_str = latest_bar.timestamp.strftime("%Y%m%d_%H%M%S")
        setup_id = (
            f"setup_xauusd_orb_reversal_{market_state.symbol}_{market_state.timeframe.value}_"
            f"{direction.name}_{ts_str}"
        )
        self.diagnostics.record_setup_generated()
        return TradeSetup(
            setup_id=setup_id,
            symbol=market_state.symbol,
            timeframe=market_state.timeframe,
            direction=direction,
            entry_zone=(round(entry, 5), round(entry, 5)),
            stop_zone=(round(stop, 5), round(stop, 5)),
            target_zone=(round(tp, 5), round(tp, 5)),
            confidence_score=1.0,
            confluence=[
                f"{direction_label} OR liquidity sweep @ {signal_time}",
                f"Displacement + FVG retest, entry @ {entry:.2f}",
                f"SL: sweep extreme + ATR buffer @ {stop:.2f}",
                f"TP: fixed {self.config.fixed_tp_r:g}R @ {tp:.2f}",
            ],
            trigger_reason=(
                f"{direction_label} 09:30 OR liquidity-sweep reversal: swept @ {signal_time}, "
                f"FVG retest entry {entry:.2f}, SL {stop:.2f}, fixed {self.config.fixed_tp_r:g}R TP {tp:.2f}"
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

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates the latest M15 bar against the day-scoped "OR sweep +
        displacement + FVG retest" state machine (Setup B only).
        """
        self.diagnostics.record_evaluation()
        cfg = self.config

        latest_bar = market_state.get_latest_bar()
        if latest_bar is None:
            return self._reject(RejectionReason.NO_LATEST_BAR)

        self._update_atr(latest_bar)

        local_dt = latest_bar.timestamp.astimezone(NY)
        local_date = local_dt.date()
        local_time = local_dt.time()

        if local_date != self._current_date:
            self._reset_day_state()
            self._current_date = local_date

        if local_time < cfg.or_start:
            return self._reject(RejectionReason.NOT_IN_SESSION)

        if self._clean_session_open is None:
            self._clean_session_open = local_time == cfg.or_start
        if not self._clean_session_open:
            return self._reject(RejectionReason.NOT_IN_SESSION)

        if not self._or_captured:
            self._or_high, self._or_low = latest_bar.high, latest_bar.low
            self._session_bars.append(latest_bar)
            self._or_captured = True
            return self._reject(RejectionReason.RANGE_NOT_READY)

        if self._atr is None:
            return self._reject(RejectionReason.WARMUP)

        if self._trades_today >= cfg.max_trades_per_day:
            return self._reject(RejectionReason.TRADE_ALREADY_TAKEN)

        if not (cfg.or_start < local_time < cfg.entry_window_end):
            return self._reject(RejectionReason.NOT_IN_SESSION)

        self._session_bars.append(latest_bar)
        i = len(self._session_bars) - 1
        bars = self._session_bars
        atr = self._atr

        # === BUY reversal: sweep of OR Low, then bullish FVG retest ===
        if not self._buy_reversal_used:
            if self._sweep_down_idx is None:
                if latest_bar.low <= self._or_low and latest_bar.close > self._or_low:
                    self._sweep_down_idx, self._sweep_down_low = i, latest_bar.low
            elif i - self._sweep_down_idx > cfg.reversal_lookback_bars:
                self._buy_reversal_used = True
                self._sweep_down_idx = self._sweep_down_low = None
            elif i >= self._sweep_down_idx + 2:
                mid = bars[i - 1]
                min_gap = cfg.fvg_min_gap_atr * atr
                disp_ok = _is_displacement(mid, atr, bullish=True, mult=cfg.displacement_atr_mult)
                gap = bars[i].low - bars[i - 2].high
                if gap >= min_gap and disp_ok:
                    zone_top = bars[i].low
                    sl = self._sweep_down_low - cfg.sl_buffer_atr * atr
                    signal_time = bars[self._sweep_down_idx].timestamp
                    self._buy_reversal_used = True
                    self._sweep_down_idx = self._sweep_down_low = None
                    risk = zone_top - sl
                    if risk <= 0 or risk > cfg.max_risk_atr_mult * atr:
                        return self._reject(RejectionReason.RISK_OUT_OF_BOUNDS)
                    tp = zone_top + cfg.fixed_tp_r * risk
                    self._trades_today += 1
                    return self._build_setup(
                        market_state, SignalDirection.BUY, zone_top, sl, tp, signal_time, latest_bar,
                    )

        # === SELL reversal: sweep of OR High, then bearish FVG retest ===
        if not self._sell_reversal_used:
            if self._sweep_up_idx is None:
                if latest_bar.high >= self._or_high and latest_bar.close < self._or_high:
                    self._sweep_up_idx, self._sweep_up_high = i, latest_bar.high
            elif i - self._sweep_up_idx > cfg.reversal_lookback_bars:
                self._sell_reversal_used = True
                self._sweep_up_idx = self._sweep_up_high = None
            elif i >= self._sweep_up_idx + 2:
                mid = bars[i - 1]
                min_gap = cfg.fvg_min_gap_atr * atr
                disp_ok = _is_displacement(mid, atr, bullish=False, mult=cfg.displacement_atr_mult)
                gap = bars[i - 2].low - bars[i].high
                if gap >= min_gap and disp_ok:
                    zone_bottom = bars[i].high
                    sl = self._sweep_up_high + cfg.sl_buffer_atr * atr
                    signal_time = bars[self._sweep_up_idx].timestamp
                    self._sell_reversal_used = True
                    self._sweep_up_idx = self._sweep_up_high = None
                    risk = sl - zone_bottom
                    if risk <= 0 or risk > cfg.max_risk_atr_mult * atr:
                        return self._reject(RejectionReason.RISK_OUT_OF_BOUNDS)
                    tp = zone_bottom - cfg.fixed_tp_r * risk
                    self._trades_today += 1
                    return self._build_setup(
                        market_state, SignalDirection.SELL, zone_bottom, sl, tp, signal_time, latest_bar,
                    )

        return self._reject(RejectionReason.NO_SWEEP)
