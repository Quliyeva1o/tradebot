"""FirstFvg15mStrategy: live port of the "09:30 + 15m + 2R" First FVG variant
validated (with real spread) in scripts/nas100_first_fvg_15m_backtest.py /
scripts/first_fvg_15m_spread_backtest.py -- see FIRST_FVG_15M_SPREAD_REPORT.md
for the full derivation (PF 1.01 over 5y / 1.16 over 1y, n=1001/198, the only
First FVG configuration that survived spread on both large-sample windows).

Deliberately NOT a variant of strategy/midnight_fvg.py (MidnightFvgStrategy)
despite the shared "first FVG of the session" shape -- the two validated
strategies differ in every rule that matters: session anchor (09:30 NY, not
00:00), timeframe (M15, not M1), SL source (the 3-candle pattern's MIDDLE/
displacement candle's own BODY -- min/max of open,close -- not its wick, and
not the candle before it), TP (fixed 2R, not 2.5R), and no minimum-gap-size
filter (the batch script accepts any bullish/bearish 3-candle gap, however
small -- confirmed by reading find_first_fvg() in
scripts/nas100_first_fvg_15m_backtest.py, which has no min-gap constant at
all, unlike scripts/first_fvg_backtest.py's MIN_GAP_POINTS=3.0). Porting
MidnightFvgStrategy's rules with the session/timeframe swapped would silently
validate a DIFFERENT, unbacktested strategy.

Day-state-machine pattern (same shape as MidnightFvgStrategy): all state is
scoped to one NY calendar day and reset when the date changes. No
cross-session tail buffer is needed here (unlike Midnight's cross-midnight
edge case) since the session anchor (09:30) is never adjacent to the
previous day's close.

Same-bar-as-entry stop-out (SESSION_HANDOFF.md #2.2's class of bug) is a
BACKTEST-SIMULATION concern only, not a live-class one: this class only ever
proposes a TradeSetup with an entry/stop/target; a real broker (or
PaperBroker) manages the actual SL/TP fill from the moment the order is
placed, so there is no "next bar" gap to close here -- see the batch
script's own docstring assumption #3 for why the backtest needed to simulate
that explicitly and this class does not.

Uses this repo's own smc/fvg.py-style zone math directly (3-candle
wick-overlap check) rather than smc/fvg.py's FVGDetector class, because the
validated batch script's find_first_fvg() has NO minimum-gap-size filter and
FVGDetector always requires one (min_gap_pips) -- reusing FVGDetector here
would silently reintroduce a filter the validated strategy does not have.

Safety: this class only ever RETURNS a TradeSetup candidate; it never
places an order -- see run_live_first_fvg_15m.py.
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
class FirstFvg15mConfig:
    """Configuration for FirstFvg15mStrategy. Defaults match the validated
    scripts/nas100_first_fvg_15m_backtest.py config exactly -- see module
    docstring.

    Attributes:
        session_start: NY local time the day's FVG search begins (default
            09:30). No session_end -- the search (and the single retest
            attempt) runs for the rest of the NY calendar day, matching the
            batch script's `session = day_df[time >= session_start]` (no
            upper bound other than day rollover).
        fixed_tp_r: Take-profit as a multiple of the SL risk distance.
            Default 2.0 -- the only R multiple that survived spread on both
            the 5y and 1y windows (see FIRST_FVG_15M_SPREAD_REPORT.md; 3R
            was tested and is WORSE on every metric, do not "upgrade" to it).
    """

    session_start: time = time(9, 30)
    fixed_tp_r: float = 2.0

    def __post_init__(self) -> None:
        require_positive(self.fixed_tp_r, "fixed_tp_r")


class FirstFvg15mStrategy(TradeSetupStrategy):
    """Live port of the "First FVG, 09:30 + 15m + 2R" strategy (see module
    docstring).
    """

    def __init__(self, config: FirstFvg15mConfig | None = None) -> None:
        self.config = config or FirstFvg15mConfig()
        self.diagnostics = StrategyDiagnostics()
        self._current_date: date | None = None
        self._reset_day_state()

    def _reset_day_state(self) -> None:
        self._session_bars: list[Bar] = []
        self._fvg_found = False
        self._fvg_direction: SignalDirection | None = None
        self._zone_top: float | None = None  # upper edge of the FVG box
        self._zone_bottom: float | None = None  # lower edge of the FVG box
        self._stop: float | None = None
        self._trade_taken = False
        # None = not yet determined today; set on the first bar at/after
        # session_start. Must equal cfg.session_start EXACTLY (matching
        # scripts/nas100_first_fvg_15m_backtest.py's `session.index[0] ==
        # SESSION_START` guard) -- otherwise the whole day is skipped. This
        # is what keeps a market-open day (Sunday evening resume, a holiday,
        # a data gap) whose first available bar lands well after 09:30 from
        # being wrongly treated as a valid, if-late, session: without this
        # guard the strategy would scan whatever bars happen to arrive first
        # each day as if they were "09:30 onward", firing on gap opens the
        # validated batch script explicitly excludes (confirmed via a
        # side-by-side replay: this fixed 9 false Sunday-evening trades a
        # first draft of this class produced against the batch script).
        self._clean_session_open: bool | None = None

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

    def _try_find_fvg(self) -> None:
        """Scans self._session_bars (this NY day's 09:30-onward bars so far)
        for the first classic 3-candle imbalance, mirroring
        scripts/nas100_first_fvg_15m_backtest.py's find_first_fvg() exactly:
        no minimum gap size, stop = the MIDDLE candle's own body (not wick).
        """
        bars = self._session_bars
        n = len(bars)
        if n < 3:
            return
        for i in range(2, n):
            prev2, mid, cur = bars[i - 2], bars[i - 1], bars[i]
            if cur.low > prev2.high:
                self._fvg_found = True
                self._fvg_direction = SignalDirection.BUY
                self._zone_top, self._zone_bottom = cur.low, prev2.high
                self._stop = min(mid.open, mid.close)
                return
            if cur.high < prev2.low:
                self._fvg_found = True
                self._fvg_direction = SignalDirection.SELL
                self._zone_top, self._zone_bottom = prev2.low, cur.high
                self._stop = max(mid.open, mid.close)
                return

    def evaluate(self, market_state: MarketState) -> TradeSetup | None:
        """Evaluates the latest M15 bar against the day-scoped "first FVG
        after 09:30, single retest, fixed 2R" state machine.
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

        if self._trade_taken:
            return self._reject(RejectionReason.TRADE_ALREADY_TAKEN)

        if local_time < cfg.session_start:
            return self._reject(RejectionReason.NOT_IN_SESSION)

        if self._clean_session_open is None:
            self._clean_session_open = local_time == cfg.session_start
        if not self._clean_session_open:
            return self._reject(RejectionReason.NOT_IN_SESSION)

        if not self._fvg_found:
            self._session_bars.append(latest_bar)
            self._try_find_fvg()
            if self._fvg_found:
                # This bar is the FVG's own confirming (3rd) candle -- the
                # batch script's retest search starts strictly AFTER it
                # (start = confirm_i + 1), so it must never also be checked
                # as a retest candidate on this same tick (same guard as
                # MidnightFvgStrategy's identical case).
                return self._reject(RejectionReason.NO_RETEST)
            return self._reject(RejectionReason.NO_MATCHING_FVG)

        direction = self._fvg_direction
        zone_top, zone_bottom, stop = self._zone_top, self._zone_bottom, self._stop
        entry: float | None = None
        if direction == SignalDirection.BUY and latest_bar.low <= zone_top:
            # Near edge on approach from above; the gap may open below the
            # edge, so the fill can never be better than this bar's own high.
            entry = min(zone_top, latest_bar.high)
        elif direction == SignalDirection.SELL and latest_bar.high >= zone_bottom:
            entry = max(zone_bottom, latest_bar.low)

        if entry is None:
            return self._reject(RejectionReason.NO_RETEST)

        risk_dist = abs(entry - stop)
        if risk_dist <= 0.0:
            return self._reject(RejectionReason.NON_POSITIVE_RISK)

        tp = entry + cfg.fixed_tp_r * risk_dist if direction == SignalDirection.BUY else entry - cfg.fixed_tp_r * risk_dist

        self._trade_taken = True

        direction_label = "Bullish" if direction == SignalDirection.BUY else "Bearish"
        ts_str = latest_bar.timestamp.strftime("%Y%m%d_%H%M%S")
        setup_id = f"setup_first_fvg_15m_{market_state.symbol}_{market_state.timeframe.value}_{direction.name}_{ts_str}"

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
                f"{direction_label} first FVG after {cfg.session_start} NY",
                f"Entry: touch @ {entry:.2f}",
                f"SL: displacement candle body @ {stop:.2f}",
                f"TP: fixed {cfg.fixed_tp_r:g}R @ {tp:.2f}",
            ],
            trigger_reason=(
                f"{direction_label} First FVG (09:30/15m) retest: entered at {entry:.2f}, "
                f"SL {stop:.2f}, fixed {cfg.fixed_tp_r:g}R TP {tp:.2f}"
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
