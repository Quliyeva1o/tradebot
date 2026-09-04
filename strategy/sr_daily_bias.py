"""SrDailyBiasStrategy: live port of pine scriptlerim/SR_Daily_Bias_Strategy.pine
(liquidity-TP variant -- see scripts/sr_daily_bias_backtest_liquidity_tp.py,
which this class mirrors bar-for-bar) validated over ~5 years of M15 history
across several symbols (XAUUSD and NAS100 were the two candidates that came
out of that backtest with consistent, positive out-of-sample performance;
USDJPY/EURUSD/GBPUSD did not and are NOT recommended -- see the session's
own risk-analysis notes for the calibrated risk-per-trade this class was
sized against).

Cross-timeframe input (Daily Bias) CANNOT be derived from the M15-only
MarketState this strategy's evaluate() receives (TradeSetupStrategy's
contract is exactly one MarketState) -- same constraint documented in
strategy/ny_open_accumulation_breakout.py's module docstring, and solved the
same way: the caller (a live-loop script) fetches D1 bars ONCE and pushes
the result in via set_daily_bias_context() before evaluate() is called for
that calendar day. If no context has been set for the CURRENT date,
evaluate() rejects every bar that day (NO_DAILY_BIAS_YET) rather than
trading on a stale prior day's bias.

Unlike MidnightFvgStrategy (one session window, one trade per day),
this strategy has NO session-window gate and NO "one trade per day" cap --
it evaluates every closed M15 bar all day, every day, and may propose a new
setup on any bar. The ONE-POSITION-AT-A-TIME rule is enforced by the CALLER
(same pattern as run_live_midnight_fvg.py's run_once(): the live loop skips
calling evaluate() for a new trade while a position is already open), not by
this class -- so this class carries no internal "already traded" flag.

KNOWN FIDELITY GAP vs. the batch backtest (documented, not hidden): the
batch backtest's own one-position-at-a-time loop (`if in_position: continue`
in scripts/sr_daily_bias_backtest_liquidity_tp.py) skips proposing ANY new
setup for as long as a position stays open, but keeps tracking pivots/
broken-levels/the liquidity pool underneath. This class has no equivalent
pause -- since evaluate() is only ever told about ONE MarketState and has no
visibility into whether a real position is currently open, it keeps
proposing setups on every bar regardless. In practice this is harmless for
what actually gets TRADED (run_once() only ever acts on the FINAL bar of a
replay, and only when the broker reports no open position at invocation
time -- see run_live_sr_bias.py), but it means this class's internal state
(especially the broken-level/retest tracking) can accumulate slightly
differently over a long unbroken replay than the exact backtest would for
the same historical stretch -- e.g. a Retest setup that the backtest would
never have reached (because a still-open position from an earlier Breakout
blocked it) can appear here. Confirmed harmless in validation (no crashes,
no malformed setups) but flagged since it's a real behavioral divergence
from the validated 5-year backtest, not merely a performance shortcut.

Indicators (ATR/ADX/volume-SMA) and swing-pivot/liquidity-pool state are all
maintained INCREMENTALLY across successive evaluate() calls (one bar at a
time, in chronological order), mirroring exactly what the Wilder-smoothing
recursion in scripts/sr_daily_bias_backtest_liquidity_tp.py computes via
pandas .ewm(adjust=False) -- same formula, just one bar at a time instead of
vectorized. As with every other strategy in this codebase, the live runner
(run_live_sr_bias.py) replays a bounded lookback of FETCHED bars through a
FRESH instance of this class every invocation (nothing persists between
process invocations); a long-enough lookback (the runner defaults to ~120
days of M15) lets these Wilder-smoothed values converge close to their
"true" long-run values before the newest (decision) bar, since exponential
smoothing forgets a stale seed quickly.

Safety: this class only ever RETURNS a TradeSetup candidate; it never places
an order -- see run_live_sr_bias.py / run_live_demo.py.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date
from zoneinfo import ZoneInfo

from core.models import Bar, SignalDirection, Timeframe
from core.validation import require_non_negative, require_positive
from market_structure.structure_models import MarketState
from research.regime_analysis import RegimeType, analyze_regime
from strategy.diagnostics import RejectionReason, StrategyDiagnostics
from strategy.interfaces import TradeSetupStrategy
from strategy.models import TradeSetup

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class DailyBiasContext:
    """Cross-timeframe input the caller computes once per calendar day from
    D1 bars and pushes in via SrDailyBiasStrategy.set_daily_bias_context() --
    same pattern as strategy/ny_open_accumulation_breakout.py's DailyContext.

    Attributes:
        for_date: The calendar date this context applies to. evaluate()
            only trusts `bias` when the bar's own date matches this.
        bias: 1 = bullish, -1 = bearish, 0 = neutral (no trades that day) --
            the last FULLY CLOSED daily bar's close vs Daily EMA(ema_len),
            with a percentage dead-zone around the EMA counting as neutral.
            No lookahead into for_date's own still-forming daily candle.
    """

    for_date: date
    bias: int


def compute_daily_bias_context(
    daily_bars: list[Bar], for_date: date, ema_len: int = 20, neutral_pct: float = 0.15
) -> DailyBiasContext | None:
    """Derives for_date's DailyBiasContext from a chronological D1 bar history.

    Args:
        daily_bars: Chronologically sorted D1 bars (any bars dated >=
            for_date are ignored -- only fully-closed prior days count).
        for_date: The calendar date to derive bias for.
        ema_len: EMA period on daily closes.
        neutral_pct: Percentage dead-zone around the EMA; a close inside it
            counts as neutral bias (no trades).

    Returns:
        None if there isn't at least ema_len+1 fully-closed daily bars
        strictly before for_date yet (not enough warmup) -- caller should
        treat this the same as a neutral/no-signal day (see
        RejectionReason.NO_DAILY_BIAS_YET).
    """
    closes = [b.close for b in daily_bars if b.timestamp.date() < for_date]
    if len(closes) < ema_len + 1:
        return None
    alpha = 2 / (ema_len + 1)
    ema = closes[0]
    for c in closes[1:]:
        ema = alpha * c + (1 - alpha) * ema
    last_close = closes[-1]
    upper = ema * (1 + neutral_pct / 100)
    lower = ema * (1 - neutral_pct / 100)
    bias = 1 if last_close > upper else (-1 if last_close < lower else 0)
    return DailyBiasContext(for_date=for_date, bias=bias)


@dataclass(frozen=True)
class SrDailyBiasConfig:
    """Configuration for SrDailyBiasStrategy. Defaults match
    pine scriptlerim/SR_Daily_Bias_Strategy.pine's own input defaults (and
    the Python backtest that validated them) exactly -- see that file's
    per-input tooltips for the rationale behind each default.
    """

    daily_bias_len: int = 20
    bias_neutral_pct: float = 0.15
    swing_len: int = 10
    min_sr_dist_atr: float = 1.5
    touch_tolerance_atr: float = 0.25
    rejection_wick_ratio: float = 0.4
    rejection_close_pos: float = 0.6
    require_vol_on_bounce: bool = True
    breakout_buffer_atr: float = 0.15
    breakout_confirm_bars: int = 1
    retest_max_bars: int = 30
    vol_sma_len: int = 20
    vol_multiplier: float = 1.3
    use_adx_filter: bool = True
    adx_len: int = 14
    adx_threshold: float = 35.0
    atr_len: int = 14
    sl_buffer_atr: float = 0.2
    min_risk_atr: float = 0.3
    max_risk_atr: float = 6.0
    min_reward_atr: float = 0.5
    fixed_rr: float | None = None  # None = liquidity-zone TP (validated variant); set a float to use fixed-R TP instead
    require_ranging_regime: bool = False  # Opt-in, OFF by default -- see FirstFvg15mConfig's docstring for the same flag; ADVANCED_VALIDATION_REPORT.md #3/#3.1 found this strategy's edge concentrated in RANGING too (improved 7/10 walk-forward folds), not yet forward-validated.
    regime_window_bars: int = 200  # research.regime_analysis.analyze_regime()'s own default; not re-tuned.

    def __post_init__(self) -> None:
        """Validates parameter ranges, matching the Pine script's own
        input(...) minval= bounds field-for-field: period/count-like fields
        and hard floors must be strictly positive, while buffer/tolerance-
        style fields the Pine script declares with minval=0.0 (meaning "off"
        is a valid setting) are only required to be non-negative here.

        Raises:
            ValueError: If a strictly-positive field is <=0, or a
                non-negative field is <0.
        """
        for name in (
            "daily_bias_len", "swing_len", "min_sr_dist_atr", "breakout_confirm_bars",
            "retest_max_bars", "vol_sma_len", "vol_multiplier", "adx_len",
            "adx_threshold", "atr_len", "min_risk_atr", "max_risk_atr", "min_reward_atr",
            "regime_window_bars",
        ):
            require_positive(getattr(self, name), name)
        for name in (
            "bias_neutral_pct", "touch_tolerance_atr", "rejection_wick_ratio",
            "rejection_close_pos", "breakout_buffer_atr", "sl_buffer_atr",
        ):
            require_non_negative(getattr(self, name), name)
        if self.fixed_rr is not None:
            require_positive(self.fixed_rr, "fixed_rr")


class SrDailyBiasStrategy(TradeSetupStrategy):
    """Live port of the Support/Resistance + Daily Bias strategy (see module
    docstring). Call set_daily_bias_context() once per calendar day (before
    the day's first evaluate() call) with a fresh DailyBiasContext.
    """

    def __init__(self, config: SrDailyBiasConfig | None = None) -> None:
        """Initializes the strategy with a config (defaults match the
        validated Pine script / backtest -- see SrDailyBiasConfig).
        """
        self.config = config or SrDailyBiasConfig()
        self.diagnostics = StrategyDiagnostics()
        self._daily_bias_context: DailyBiasContext | None = None

        self._prev_bar: Bar | None = None
        self._atr: float | None = None
        self._smoothed_tr: float | None = None
        self._smoothed_plus_dm: float | None = None
        self._smoothed_minus_dm: float | None = None
        self._adx_raw_count: int = 0  # DX values seen, for ADX's own warmup
        self._smoothed_dx: float | None = None
        self._vol_window: deque[float] = deque(maxlen=self.config.vol_sma_len)

        pivot_window = self.config.swing_len * 2 + 1
        self._pivot_bars: deque[Bar] = deque(maxlen=pivot_window)
        self._bar_index: int = -1  # running counter, for broken-level age

        self._resistance: float | None = None
        self._support: float | None = None
        self._active_highs: list[float] = []
        self._active_lows: list[float] = []
        self._broken_res_level: float | None = None
        self._broken_res_bar: int | None = None
        self._broken_sup_level: float | None = None
        self._broken_sup_bar: int | None = None
        self._recent_closes: deque[float] = deque(maxlen=max(self.config.breakout_confirm_bars, 1))

    def set_daily_bias_context(self, context: DailyBiasContext) -> None:
        """Pushes this calendar day's Daily Bias input (see DailyBiasContext).
        Call once per NY/broker calendar day, before that day's first
        evaluate() -- see module docstring.
        """
        self._daily_bias_context = context

    def reset(self) -> None:
        """Resets diagnostics and all state (fresh backtest/live run)."""
        self.__init__(self.config)  # noqa: PLC2801  -- deliberate full re-init, same pattern as a fresh instance

    def recommended_max_holding_bars(self, timeframe: Timeframe) -> int | None:
        """No holding-bars recommendation -- exit is TP/SL-only, managed by
        TradeManager/broker like every other strategy here.
        """
        return None

    def _reject(self, reason: RejectionReason) -> None:
        self.diagnostics.record_rejection(reason)
        return None

    def _update_atr_adx(self, bar: Bar) -> None:
        cfg = self.config
        if self._prev_bar is None:
            self._prev_bar = bar
            return
        prev = self._prev_bar
        tr = max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close))
        up_move = bar.high - prev.high
        down_move = prev.low - bar.low
        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

        alpha_atr = 1 / cfg.atr_len
        self._atr = tr if self._atr is None else (alpha_atr * tr + (1 - alpha_atr) * self._atr)

        alpha_adx = 1 / cfg.adx_len
        self._smoothed_tr = tr if self._smoothed_tr is None else (alpha_adx * tr + (1 - alpha_adx) * self._smoothed_tr)
        self._smoothed_plus_dm = plus_dm if self._smoothed_plus_dm is None else (alpha_adx * plus_dm + (1 - alpha_adx) * self._smoothed_plus_dm)
        self._smoothed_minus_dm = minus_dm if self._smoothed_minus_dm is None else (alpha_adx * minus_dm + (1 - alpha_adx) * self._smoothed_minus_dm)

        if self._smoothed_tr:
            plus_di = 100 * self._smoothed_plus_dm / self._smoothed_tr
            minus_di = 100 * self._smoothed_minus_dm / self._smoothed_tr
            di_sum = plus_di + minus_di
            dx = 100 * abs(plus_di - minus_di) / di_sum if di_sum else 0.0
        else:
            dx = 0.0
        self._smoothed_dx = dx if self._smoothed_dx is None else (alpha_adx * dx + (1 - alpha_adx) * self._smoothed_dx)
        self._adx_raw_count += 1

        self._prev_bar = bar

    @property
    def _adx(self) -> float | None:
        if self._adx_raw_count < self.config.adx_len:
            return None
        return self._smoothed_dx

    def _update_pivots(self) -> None:
        """Checks whether the CENTER bar of the current pivot window is a
        confirmed swing high/low (ta.pivothigh/pivotlow semantics: requires
        swing_len bars on both sides), and if so, updates resistance/support
        and pushes it onto the active liquidity pool.
        """
        cfg = self.config
        window = self._pivot_bars
        if len(window) < window.maxlen:
            return
        mid = cfg.swing_len
        bars = list(window)
        center = bars[mid]
        if center.high == max(b.high for b in bars):
            self._resistance = center.high
            self._active_highs.append(center.high)
        if center.low == min(b.low for b in bars):
            self._support = center.low
            self._active_lows.append(center.low)

    def _mitigate_liquidity(self, bar: Bar) -> None:
        self._active_highs = [lvl for lvl in self._active_highs if lvl > bar.high]
        self._active_lows = [lvl for lvl in self._active_lows if lvl < bar.low]

    @staticmethod
    def _rejection(bar: Bar, wick_ratio: float, close_pos: float, bullish: bool) -> bool:
        range_ = bar.high - bar.low
        if range_ <= 0:
            return False
        if bullish:
            wick = min(bar.open, bar.close) - bar.low
            pos = (bar.close - bar.low) / range_
        else:
            wick = bar.high - max(bar.open, bar.close)
            pos = (bar.high - bar.close) / range_
        return wick >= wick_ratio * range_ and pos >= close_pos

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates the latest M15 bar against the Support/Resistance +
        Daily Bias rules (bounce / breakout / retest -- see module
        docstring). Requires set_daily_bias_context() to have been called
        for the bar's own calendar date.
        """
        self.diagnostics.record_evaluation()
        cfg = self.config

        bar = market_state.get_latest_bar()
        if bar is None:
            return self._reject(RejectionReason.NO_LATEST_BAR)

        self._bar_index += 1
        this_bar_index = self._bar_index

        ctx = self._daily_bias_context
        if ctx is None or ctx.for_date != bar.timestamp.date():
            # Still update all rolling state below so nothing desyncs once a
            # context does arrive -- only the trading decision is gated.
            bias = None
        else:
            bias = ctx.bias

        # Captured BEFORE _update_atr_adx() below, which reassigns
        # self._prev_bar to THIS bar -- reading it after that call would
        # silently compare the current close against itself, making the
        # breakout "freshness" check (below) permanently false.
        prev_close = self._prev_bar.close if self._prev_bar is not None else bar.close

        # Update rolling indicator/pivot/liquidity state FIRST, unconditionally
        # -- mirrors the backtest's per-bar loop, which tracks pivots/broken
        # levels/liquidity every bar regardless of that bar's bias/warmup
        # status (see scripts/sr_daily_bias_backtest_liquidity_tp.py).
        self._update_atr_adx(bar)
        self._pivot_bars.append(bar)
        self._update_pivots()
        self._mitigate_liquidity(bar)
        self._vol_window.append(bar.volume)
        self._recent_closes.append(bar.close)

        if self._broken_res_bar is not None and this_bar_index - self._broken_res_bar > cfg.retest_max_bars:
            self._broken_res_level, self._broken_res_bar = None, None
        if self._broken_sup_bar is not None and this_bar_index - self._broken_sup_bar > cfg.retest_max_bars:
            self._broken_sup_level, self._broken_sup_bar = None, None

        if bias is None:
            return self._reject(RejectionReason.NO_DAILY_BIAS_YET)
        if bias == 0:
            return self._reject(RejectionReason.NEUTRAL_BIAS)

        atr = self._atr
        if atr is None or self._resistance is None or self._support is None:
            return self._reject(RejectionReason.WARMUP)

        sr_dist_ok = (self._resistance - self._support) >= cfg.min_sr_dist_atr * atr
        if not sr_dist_ok:
            return self._reject(RejectionReason.SR_TOO_CLOSE)

        vol_sma = sum(self._vol_window) / len(self._vol_window) if len(self._vol_window) == self._vol_window.maxlen else None
        vol_confirmed = vol_sma is not None and bar.volume >= vol_sma * cfg.vol_multiplier
        strong_trend = cfg.use_adx_filter and self._adx is not None and self._adx >= cfg.adx_threshold

        bull_rej = self._rejection(bar, cfg.rejection_wick_ratio, cfg.rejection_close_pos, bullish=True)
        bear_rej = self._rejection(bar, cfg.rejection_wick_ratio, cfg.rejection_close_pos, bullish=False)

        touched_support = bar.low <= self._support + cfg.touch_tolerance_atr * atr
        touched_resistance = bar.high >= self._resistance - cfg.touch_tolerance_atr * atr

        long_bounce = bias == 1 and touched_support and bull_rej and (not cfg.require_vol_on_bounce or vol_confirmed) and not strong_trend
        short_bounce = bias == -1 and touched_resistance and bear_rej and (not cfg.require_vol_on_bounce or vol_confirmed) and not strong_trend

        closes_above_res = min(self._recent_closes) > self._resistance + cfg.breakout_buffer_atr * atr if len(self._recent_closes) == self._recent_closes.maxlen else False
        closes_below_sup = max(self._recent_closes) < self._support - cfg.breakout_buffer_atr * atr if len(self._recent_closes) == self._recent_closes.maxlen else False
        fresh_up = closes_above_res and not (prev_close > self._resistance + cfg.breakout_buffer_atr * atr)
        fresh_down = closes_below_sup and not (prev_close < self._support - cfg.breakout_buffer_atr * atr)

        bullish_breakout = bias == 1 and fresh_up and bool(vol_confirmed)
        bearish_breakout = bias == -1 and fresh_down and bool(vol_confirmed)

        # Captured BEFORE this bar's own breakout (if any) sets a fresh level:
        # a retest may only consume a level broken on a PRIOR bar, never the
        # level this same bar just broke -- otherwise a breakout bar whose
        # low/high also happens to wick back into the touch-tolerance band
        # would immediately null the pending level, discarding it before any
        # later, genuine retest bar ever gets to use it.
        retest_long_level = self._broken_res_level
        retest_short_level = self._broken_sup_level

        if bullish_breakout:
            self._broken_res_level, self._broken_res_bar = self._resistance, this_bar_index
        if bearish_breakout:
            self._broken_sup_level, self._broken_sup_bar = self._support, this_bar_index

        retest_long = (
            bias == 1 and retest_long_level is not None
            and retest_long_level - cfg.touch_tolerance_atr * atr <= bar.low <= retest_long_level + cfg.touch_tolerance_atr * atr
            and bull_rej
        )
        retest_short = (
            bias == -1 and retest_short_level is not None
            and retest_short_level - cfg.touch_tolerance_atr * atr <= bar.high <= retest_short_level + cfg.touch_tolerance_atr * atr
            and bear_rej
        )
        if retest_long:
            self._broken_res_level, self._broken_res_bar = None, None
        if retest_short:
            self._broken_sup_level, self._broken_sup_bar = None, None

        long_setup = long_bounce or bullish_breakout or retest_long
        short_setup = short_bounce or bearish_breakout or retest_short

        if long_setup:
            sl_base = self._support if long_bounce else (self._resistance if bullish_breakout else retest_long_level)
            direction = SignalDirection.BUY
            entry_type = "Bounce" if long_bounce else ("Breakout" if bullish_breakout else "Retest")
        elif short_setup:
            sl_base = self._resistance if short_bounce else (self._support if bearish_breakout else retest_short_level)
            direction = SignalDirection.SELL
            entry_type = "Bounce" if short_bounce else ("Breakout" if bearish_breakout else "Retest")
        else:
            return self._reject(RejectionReason.STRONG_TREND_BLOCKS_BOUNCE if strong_trend else RejectionReason.NO_BREAKOUT)

        entry = bar.close
        if direction == SignalDirection.BUY:
            sl = sl_base - cfg.sl_buffer_atr * atr
            risk_dist = entry - sl
        else:
            sl = sl_base + cfg.sl_buffer_atr * atr
            risk_dist = sl - entry
        if risk_dist <= 0 or not (cfg.min_risk_atr * atr <= risk_dist <= cfg.max_risk_atr * atr):
            return self._reject(RejectionReason.RISK_OUT_OF_BOUNDS)

        if cfg.fixed_rr is not None:
            tp = entry + risk_dist * cfg.fixed_rr if direction == SignalDirection.BUY else entry - risk_dist * cfg.fixed_rr
        else:
            if direction == SignalDirection.BUY:
                candidates = [lvl for lvl in self._active_highs if lvl > entry]
                tp = min(candidates) if candidates else None
            else:
                candidates = [lvl for lvl in self._active_lows if lvl < entry]
                tp = max(candidates) if candidates else None
            if tp is None:
                return self._reject(RejectionReason.NO_LIQUIDITY_TARGET)
            reward = abs(tp - entry)
            if reward < cfg.min_reward_atr * atr:
                return self._reject(RejectionReason.REWARD_TOO_SMALL)

        if cfg.require_ranging_regime:
            regime = analyze_regime(
                market_state.bars_view(), symbol=market_state.symbol,
                timeframe=market_state.timeframe, window_bars=cfg.regime_window_bars,
            )
            if regime.regime != RegimeType.RANGING:
                return self._reject(RejectionReason.REGIME_NOT_RANGING)

        direction_label = "Bullish" if direction == SignalDirection.BUY else "Bearish"
        ts_str = bar.timestamp.strftime("%Y%m%d_%H%M%S")
        setup_id = f"setup_sr_bias_{market_state.symbol}_{market_state.timeframe.value}_{direction.name}_{entry_type}_{ts_str}"

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
                f"{direction_label} {entry_type} against Daily Bias={'UP' if bias == 1 else 'DOWN'}",
                f"S/R: {self._support:.2f} - {self._resistance:.2f} (ATR={atr:.2f})",
                f"SL: {sl:.2f}  TP: {tp:.2f} (R={risk_dist:.2f})",
            ],
            trigger_reason=f"{direction_label} {entry_type}: entered at {entry:.2f}, SL {sl:.2f}, TP {tp:.2f}",
            invalidations=["Price closes back through the entry level in the opposite direction", "Price breaches Stop Loss zone"],
            related_structure_break=None,
            related_order_block=None,
            related_fvg=None,
            timestamp=bar.timestamp,
            strategy_name=self.__class__.__name__,
        )
