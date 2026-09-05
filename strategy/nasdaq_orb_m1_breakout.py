"""NasdaqOrbM1BreakoutStrategy: live port of scripts/nasdaq_orb_m1_breakout_backtest.py
(LONG-only, stop_mode="full"), the strategy validated across a 108-combo
sweep (9 symbols x 2 stop-modes x 6 R-values) on FundingPips-Trial M1 data,
2026-09-04. See that script's module docstring for the full spec derivation.

NOT the same strategy as strategy/opening_range_breakout.py's
OpeningRangeBreakoutStrategy ("the user's 3rd strategy") -- that one requires
a volume spike, anchors its stop to the first opening candle only, and fills
on the SAME bar's close. This class has no volume filter, anchors its stop
to the FULL Opening Range's high/low, and only detects a breakout on a bar's
close -- the actual fill (via TradeManager.open_trade -> broker.place_order,
always a real MARKET order, see execution/trade_manager.py) naturally lands
near the NEXT bar's open once this setup is submitted on the next poll,
matching the backtest's realistic "entry_price = next bar's open" convention
without any special-casing needed here.

Mechanics (M1 bars, NY session):
  1. Opening Range = the 09:30-09:45 NY window's combined high/low, built by
     accumulating every M1 bar with 09:30 <= local_time < 09:45 (mirrors the
     backtest's M15-resampled 09:30 candle; time-gated rather than a fixed
     bar count so a data gap doesn't silently shift the window).
  2. From 09:45, the FIRST M1 bar whose CLOSE closes outside the OR (>OR High
     = bullish, <OR Low = bearish) is the day's ONLY signal -- uncapped for
     the rest of the NY calendar day (no entry-window cutoff, per the
     backtest's own spec).
  3. SL ("full" stop-mode, the only mode this class implements -- the sweep
     found "half" -- OR midpoint -- inferior on almost every symbol/R combo):
     OR Low (long) / OR High (short). TP = entry +/- tp_r * risk.
  4. One trade per calendar day: `_day_trade_taken` latches True the moment
     a breakout is detected, mirroring the backtest's `day_trade_taken`
     flag, which is set regardless of whether the resulting order actually
     fills -- prevents a same-day retry in the opposite direction even if
     this setup gets rejected by the broker.
  5. LONG-only by default (`direction="long"`) -- the validated config for
     every symbol in the 2026-09-04 sweep; "short" and "both" are supported
     for completeness but were not part of that validation.

Symbol/R map from the 2026-09-04 108-combo sweep (FundingPips-Trial,
LONG-only, full stop-mode; see [[project-fundingpips-trial-account]] memory):
works well on NDX100, XAUUSD, DJI30, SPX500, GER40, JP225 (3.0-4.0R best);
does NOT clear PF>=1.0 at any R on STX50 or XAUUSD/XAGUSD-adjacent silver --
do not deploy this class on STX50 or XAGUSD without new validation.

Live-class fidelity check (2026-09-04, FundingPips-Trial, last ~6 months of
M1 data, same position-open gating pattern as backtest_xauusd_orb_live_class.py):
  - GER40 3.0R: live=133 vs batch=134 trades (0.7% gap) -- the single miss is
    the batch's very last trading day in the window, an end-of-data boundary
    artifact of the finite test slice, not a live-class divergence.
  - XAUUSD 4.0R: live=125 vs batch=120 trades (~4% gap) -- same accepted
    category of gap already documented for XauusdOrbLiquiditySweepStrategy's
    own fidelity check (see that module's docstring): this class uses the
    breakout bar's own CLOSE as its entry reference (the only price known at
    signal time in true live trading -- the next bar hasn't happened yet),
    while the batch script uses the next bar's OPEN (knowable only in
    hindsight). The resulting few-cent entry/SL/TP differences occasionally
    shift which bar resolves a trade first, which can cascade into a
    different subsequent trade being eligible or not. Using the next bar's
    open here would require peeking at unclosed future data -- a real
    lookahead bug, strictly worse than this honest, bounded gap.

NOT YET forward/paper-validated -- fresh backtest-to-live port like
XauusdOrbLiquiditySweepStrategy was. Route through a Paper runner first.

Timeframe assumption: this class assumes it is fed M1 bars specifically (the
Opening Range is built by accumulating M1 bars over a 15-minute window) --
feeding it any other timeframe computes a wrong-sized range that was never
backtested.

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
from strategy.session_utils import add_minutes

NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class NasdaqOrbM1BreakoutConfig:
    """Configuration for NasdaqOrbM1BreakoutStrategy. Defaults match the
    2026-09-04 sweep's "full" stop-mode, LONG-only convention -- see module
    docstring. `tp_r` and `symbol`-appropriate spread are the two knobs the
    sweep found actually move the result; everything else is spec-fixed.

    Attributes:
        or_start: NY local time the Opening Range window begins (09:30).
        or_minutes: Width of the Opening Range window in minutes (15,
            matching the backtest's single M15 candle).
        tp_r: Take-profit as a multiple of the SL risk distance. The sweep's
            best R varied by symbol (see module docstring) -- 3.0-4.0
            consistently beat 1.5-2.0 wherever this strategy works at all;
            pass the symbol-specific winner explicitly, this default is only
            a fallback.
        direction: Which breakout direction(s) to trade. "long" is the only
            validated choice from the 2026-09-04 sweep.
    """

    or_start: time = time(9, 30)
    or_minutes: int = 15
    tp_r: float = 3.0
    direction: str = "long"

    def __post_init__(self) -> None:
        require_positive(self.or_minutes, "or_minutes")
        require_positive(self.tp_r, "tp_r")
        if self.direction not in ("long", "short", "both"):
            raise ValueError(f"direction must be 'long', 'short', or 'both', got {self.direction!r}")


class NasdaqOrbM1BreakoutStrategy(TradeSetupStrategy):
    """Live port of the validated NASDAQ ORB M1 Breakout backtest (see module
    docstring) -- OR 09:30-09:45 NY, M1 breakout scan, "full" stop-mode.
    """

    def __init__(self, config: NasdaqOrbM1BreakoutConfig | None = None) -> None:
        self.config = config or NasdaqOrbM1BreakoutConfig()
        self._scan_start = add_minutes(self.config.or_start, self.config.or_minutes)
        self.diagnostics = StrategyDiagnostics()
        self._current_date: date | None = None
        self._reset_day_state()

    def _reset_day_state(self) -> None:
        self._or_high: float | None = None
        self._or_low: float | None = None
        self._or_ready = False
        self._day_trade_taken = False

    def reset(self) -> None:
        """Resets diagnostics and all state (fresh backtest/live run)."""
        self.diagnostics.reset()
        self._current_date = None
        self._reset_day_state()

    def recommended_max_holding_bars(self, timeframe: Timeframe) -> int | None:
        """No holding-bars recommendation -- exit is TP/SL-only, managed by
        TradeManager/broker like every other strategy here.
        """
        return None

    def _reject(self, reason: RejectionReason) -> None:
        self.diagnostics.record_rejection(reason)
        return None

    def _build_setup(
        self, market_state: MarketState, direction: SignalDirection,
        entry: float, stop: float, tp: float, latest_bar: Bar,
    ) -> TradeSetup:
        direction_label = "Bullish" if direction == SignalDirection.BUY else "Bearish"
        ts_str = latest_bar.timestamp.strftime("%Y%m%d_%H%M%S")
        setup_id = (
            f"setup_nasdaq_orb_m1_{market_state.symbol}_{market_state.timeframe.value}_"
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
                f"{direction_label} close outside {self.config.or_minutes}m Opening Range "
                f"[{self._or_low:.5f}, {self._or_high:.5f}]",
                f"SL: OR {'low' if direction == SignalDirection.BUY else 'high'} @ {stop:.5f}",
                f"TP: fixed {self.config.tp_r:g}R @ {tp:.5f}",
            ],
            trigger_reason=(
                f"{direction_label} {self.config.or_minutes}m ORB breakout: OR=[{self._or_low:.5f}, "
                f"{self._or_high:.5f}], close {entry:.5f}, SL {stop:.5f}, {self.config.tp_r:g}R TP {tp:.5f}"
            ),
            invalidations=["Price breaches Stop Loss zone (OR opposite boundary)"],
            related_structure_break=None,
            related_order_block=None,
            related_fvg=None,
            timestamp=latest_bar.timestamp,
            strategy_name=self.__class__.__name__,
        )

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates the latest M1 bar against the day-scoped "09:30-09:45 OR
        + M1 breakout" state machine.
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

        if local_time < cfg.or_start:
            return self._reject(RejectionReason.NOT_IN_SESSION)

        if local_time < self._scan_start:
            if self._or_high is None:
                self._or_high, self._or_low = latest_bar.high, latest_bar.low
            else:
                self._or_high = max(self._or_high, latest_bar.high)
                self._or_low = min(self._or_low, latest_bar.low)
            return self._reject(RejectionReason.RANGE_NOT_READY)

        if not self._or_ready:
            if self._or_high is None:
                # Scan window reached with zero OR bars ever seen this day
                # (e.g. bot started mid-session past 09:45) -- nothing to
                # measure a range against; wait for tomorrow.
                return self._reject(RejectionReason.RANGE_NOT_READY)
            self._or_ready = True

        if self._day_trade_taken:
            return self._reject(RejectionReason.TRADE_ALREADY_TAKEN)

        or_high, or_low = self._or_high, self._or_low
        bullish = latest_bar.close > or_high and cfg.direction in ("both", "long")
        bearish = latest_bar.close < or_low and cfg.direction in ("both", "short")
        if not (bullish or bearish):
            return self._reject(RejectionReason.NO_BREAKOUT)

        direction = SignalDirection.BUY if bullish else SignalDirection.SELL
        entry = latest_bar.close  # actual fill is a real market order on the NEXT poll -- see module docstring
        stop = or_low if direction == SignalDirection.BUY else or_high
        risk = abs(entry - stop)

        self._day_trade_taken = True

        if risk <= 0:
            return self._reject(RejectionReason.NON_POSITIVE_RISK)

        tp = entry + cfg.tp_r * risk if direction == SignalDirection.BUY else entry - cfg.tp_r * risk
        return self._build_setup(market_state, direction, entry, stop, tp, latest_bar)
