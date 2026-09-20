"""Liquidity sweep, reclaim, 5M BOS, retracement, 1M BOS -- the four-gate continuation model.

Rule, per NY trading day, for a LONG (a SHORT is the exact mirror, `long=False`):
  1. the day's liquidity levels are marked ONCE, at the open: 4H and 1H high/low from the
     last candle closed before 09:30, the previous NY session's high/low (PSH/PSL), and
     the overnight high/low (ONH/ONL, 18:00 to 09:30). Marking them once is what a trader
     does at the open, and it keeps an intraday level from quietly carrying later
     information back into an earlier decision;
  2. price must trade BELOW one of the sell-side levels -- the sweep;
  3. and then close back ABOVE it within `reclaim_bars` -- the reclaim. The lowest low
     between the two is the `sweep_extreme`, and it is the day's invalidation point;
  4. a 5M bar must then close above the most recent CONFIRMED 5M swing high -- the 5M BOS.
     The leg it prints, `sweep_extreme` to the highest high up to that bar, is what the
     retracement is measured against;
  5. price must retrace into the zone: the leg's 50% equilibrium, the last bullish 5M FVG
     inside the leg, or whichever comes first (`RetraceMode`);
  6. an M1 bar must then close above the most recent confirmed M1 swing high -- the 1M BOS;
  7. entry is that bar's close.

Every gate carries a deadline and every phase dies if price trades back through
`sweep_extreme`: the whole premise is that the swept low held.

A swing high is a non-repainting pivot -- `pivot_strength` bars either side, and its value
is only readable `pivot_strength` bars after it printed. The caller supplies the confirmed
swing and FVG values per bar (the backtest precomputes them for the whole series), so this
module never looks at a bar it has not been handed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from zoneinfo import ZoneInfo

from core.models import Bar, SignalDirection
from core.validation import require_non_negative, require_positive

NY = ZoneInfo("America/New_York")

# Travels as the order comment; MT5 keeps only the first 20 characters (see
# execution/mt5_broker.py), and no other bot's tag may be a prefix of this one.
STRATEGY_TAG = "setup_sweep_bos"


class RetraceMode(Enum):
    EQUILIBRIUM = "equilibrium"  # the leg's 50%
    FVG = "fvg"  # the last bullish 5M gap inside the leg
    EITHER = "either"  # whichever price reaches first


class StopMode(Enum):
    SWEEP_EXTREME = "sweep_extreme"  # beyond the wick that took the liquidity
    PULLBACK_EXTREME = "pullback_extreme"  # beyond the retracement's own low


class TargetMode(Enum):
    NEXT_LIQUIDITY = "next_liquidity"  # the nearest buy-side level above entry
    FIXED_R = "fixed_r"  # a multiple of the trade's own risk


@dataclass(frozen=True)
class LiquiditySweepBosConfig:
    session_start: time = time(9, 30)
    session_end: time = time(16, 0)  # no new setups after this; an open trade closes here
    reclaim_bars: int = 30  # M1 bars the reclaim may take
    bos5_bars: int = 24  # 5M bars the 5M BOS may take after the reclaim
    retrace_bars: int = 24  # 5M bars the retracement may take after the 5M BOS
    bos1_bars: int = 30  # M1 bars the 1M BOS may take after price enters the zone
    pivot_strength: int = 2  # bars either side of a swing, on both 5M and M1
    stop_buffer_frac: float = 0.05  # of the entry-to-invalidation distance
    retrace_mode: RetraceMode = RetraceMode.EQUILIBRIUM
    stop_mode: StopMode = StopMode.SWEEP_EXTREME
    target_mode: TargetMode = TargetMode.NEXT_LIQUIDITY
    target_r: float = 2.0  # used only by TargetMode.FIXED_R
    hold_past_session: bool = False  # True holds to the stop or target instead of closing at 16:00

    def __post_init__(self) -> None:
        for name in ("reclaim_bars", "bos5_bars", "retrace_bars", "bos1_bars", "pivot_strength"):
            require_positive(getattr(self, name), name)
        require_non_negative(self.stop_buffer_frac, "stop_buffer_frac")
        require_positive(self.target_r, "target_r")
        if self.session_end <= self.session_start:
            raise ValueError(f"session_end ({self.session_end}) must follow {self.session_start}")


@dataclass(frozen=True)
class Levels:
    """The day's marked liquidity, fixed at the session open."""

    h4_high: float
    h4_low: float
    h1_high: float
    h1_low: float
    psh: float
    psl: float
    onh: float
    onl: float

    def highs(self) -> list[tuple[str, float]]:
        return [("H4H", self.h4_high), ("H1H", self.h1_high), ("PSH", self.psh), ("ONH", self.onh)]

    def lows(self) -> list[tuple[str, float]]:
        return [("H4L", self.h4_low), ("H1L", self.h1_low), ("PSL", self.psl), ("ONL", self.onl)]

    def sweep_side(self, *, long: bool) -> list[tuple[str, float]]:
        """The levels a setup in this direction has to take out first."""
        return self.lows() if long else self.highs()

    def target_side(self, *, long: bool) -> list[tuple[str, float]]:
        """The levels a setup in this direction can aim at."""
        return self.highs() if long else self.lows()


@dataclass(frozen=True)
class SweepEntry:
    """The trade one day's setup calls for. Timestamps are UTC, like the bars."""

    direction: SignalDirection
    time: datetime
    price: float
    stop: float
    target: float
    level_name: str
    level_price: float
    sweep_extreme: float
    leg_high: float
    target_name: str

    @property
    def risk(self) -> float:
        sign = 1 if self.direction == SignalDirection.BUY else -1
        return (self.price - self.stop) * sign


def _ny_minute(ts: datetime) -> int:
    local = ts.astimezone(NY)
    return local.hour * 60 + local.minute


def _minute_of(t: time) -> int:
    return t.hour * 60 + t.minute


class DayState:
    """One NY day's progress through the four gates, fed that day's M1 bars in order.

    `on_bar` returns a SweepEntry on the bar that completes the setup and None on every
    other bar, including every bar afterwards -- one trade per day.

    The caller supplies, per bar and already confirmed:
      bos5_leg_high -- the leg high when a 5M BOS closes at this bar, else None;
      m1_swing      -- the most recent confirmed M1 swing high (low, for a short);
      fvg_edge      -- the near edge of the last bullish 5M FVG inside the leg, else None.
    """

    def __init__(self, levels: Levels, cfg: LiquiditySweepBosConfig, *, long: bool = True) -> None:
        self._levels = levels
        self._cfg = cfg
        self._long = long
        self._sign = 1 if long else -1
        self._phase = "sweep"
        self._level: tuple[str, float] | None = None
        self._sweep_extreme: float | None = None
        self._since = 0  # bars in the current phase
        self._leg_high: float | None = None
        self._zone: float | None = None  # the price that counts as "in the zone"
        self._pullback_extreme: float | None = None
        self._done = False

    def _better(self, a: float, b: float) -> bool:
        """True when `a` is further in the trade's favour than `b`."""
        return a * self._sign > b * self._sign

    def on_bar(
        self,
        bar: Bar,
        *,
        bos5_leg_high: float | None = None,
        m1_swing: float | None = None,
        fvg_edge: float | None = None,
    ) -> SweepEntry | None:
        if self._done:
            return None
        minute = _ny_minute(bar.timestamp)
        if minute < _minute_of(self._cfg.session_start):
            return None
        if minute >= _minute_of(self._cfg.session_end):
            self._done = True
            return None

        # Once the level is reclaimed, trading back through the swept extreme kills the
        # setup: its whole premise is that the low held. This cannot apply before the
        # reclaim -- a sweep running deeper is the sweep, not its invalidation.
        if self._sweep_extreme is not None and self._phase not in ("sweep", "reclaim"):
            through = bar.low < self._sweep_extreme if self._long else bar.high > self._sweep_extreme
            if through:
                self._done = True
                return None

        self._since += 1
        if self._phase == "sweep":
            return self._look_for_sweep(bar)
        if self._phase == "reclaim":
            return self._look_for_reclaim(bar)
        if self._phase == "bos5":
            return self._look_for_bos5(bar, bos5_leg_high)
        if self._phase == "retrace":
            return self._look_for_retrace(bar, fvg_edge)
        return self._look_for_bos1(bar, m1_swing)

    # -- gate 1: the sweep ------------------------------------------------

    def _look_for_sweep(self, bar: Bar) -> None:
        """Price takes out a level. The nearest one it reaches is the one that counts."""
        wick = bar.low if self._long else bar.high
        taken = [(n, p) for n, p in self._levels.sweep_side(long=self._long)
                 if self._better(p, wick)]
        if not taken:
            return None
        # Several levels can go in one bar. The DEEPEST one is the one that counts: that
        # is where the liquidity was actually taken, and closing back above it is the
        # first evidence the grab has failed. Picking a shallower level would call the
        # setup valid while price still sat under the pool it had just run.
        self._level = min(taken, key=lambda kv: kv[1] * self._sign)
        self._sweep_extreme = wick
        self._phase, self._since = "reclaim", 0
        return self._look_for_reclaim(bar)  # the same bar may already close back above

    # -- gate 2: the reclaim ----------------------------------------------

    def _look_for_reclaim(self, bar: Bar) -> None:
        assert self._level is not None and self._sweep_extreme is not None
        wick = bar.low if self._long else bar.high
        if self._better(self._sweep_extreme, wick):
            self._sweep_extreme = wick  # the sweep ran deeper
        if self._better(bar.close, self._level[1]):
            self._phase, self._since = "bos5", 0
            self._leg_high = bar.high if self._long else bar.low
            return None
        if self._since >= self._cfg.reclaim_bars:
            self._done = True
        return None

    # -- gate 3: the 5M BOS -----------------------------------------------

    def _look_for_bos5(self, bar: Bar, bos5_leg_high: float | None) -> None:
        assert self._leg_high is not None
        extreme = bar.high if self._long else bar.low
        if self._better(extreme, self._leg_high):
            self._leg_high = extreme
        if bos5_leg_high is not None:
            if self._better(bos5_leg_high, self._leg_high):
                self._leg_high = bos5_leg_high
            self._phase, self._since = "retrace", 0
            # Deliberately left unset. This bar's low belongs to the impulse, not to the
            # pullback, and seeding with it would make the "tight" stop no tighter than
            # the leg itself. The first retracement bar sets it.
            self._pullback_extreme = None
            return None
        if self._since >= self._cfg.bos5_bars * 5:  # the deadline is in 5M bars
            self._done = True
        return None

    # -- gate 4: the retracement ------------------------------------------

    def _look_for_retrace(self, bar: Bar, fvg_edge: float | None) -> None:
        assert self._leg_high is not None and self._sweep_extreme is not None
        wick = bar.low if self._long else bar.high
        if self._pullback_extreme is None or self._better(self._pullback_extreme, wick):
            self._pullback_extreme = wick

        zone = self._zone_price(fvg_edge)
        if zone is not None and not self._better(wick, zone):
            self._zone = zone
            self._phase, self._since = "bos1", 0
            return None
        if self._since >= self._cfg.retrace_bars * 5:
            self._done = True
        return None

    def _zone_price(self, fvg_edge: float | None) -> float | None:
        """The price price has to reach for the retracement to count."""
        assert self._leg_high is not None and self._sweep_extreme is not None
        eq = (self._leg_high + self._sweep_extreme) / 2.0
        # A gap only counts when it sits inside the leg. The caller hands over the most
        # recent one it knows about, which before the impulse prints its own gap is some
        # older, unrelated imbalance -- entering on that would not be this setup.
        if fvg_edge is not None and not (
            self._better(fvg_edge, self._sweep_extreme) and self._better(self._leg_high, fvg_edge)
        ):
            fvg_edge = None

        mode = self._cfg.retrace_mode
        if mode is RetraceMode.EQUILIBRIUM:
            return eq
        if mode is RetraceMode.FVG:
            return fvg_edge
        if fvg_edge is None:
            return eq
        # EITHER: whichever price meets first is the shallower of the two.
        return max(eq, fvg_edge) if self._long else min(eq, fvg_edge)

    # -- gate 5: the 1M BOS, and the order --------------------------------

    def _look_for_bos1(self, bar: Bar, m1_swing: float | None) -> SweepEntry | None:
        wick = bar.low if self._long else bar.high
        if self._pullback_extreme is None or self._better(self._pullback_extreme, wick):
            self._pullback_extreme = wick
        if m1_swing is not None and self._better(bar.close, m1_swing):
            return self._build(bar)
        if self._since >= self._cfg.bos1_bars:
            self._done = True
        return None

    def _build(self, bar: Bar) -> SweepEntry | None:
        assert self._level is not None and self._sweep_extreme is not None
        assert self._leg_high is not None and self._pullback_extreme is not None
        self._done = True

        base = (self._sweep_extreme if self._cfg.stop_mode is StopMode.SWEEP_EXTREME
                else self._pullback_extreme)
        raw_risk = (bar.close - base) * self._sign
        if raw_risk <= 0:
            return None
        stop = base - self._sign * self._cfg.stop_buffer_frac * raw_risk
        risk = (bar.close - stop) * self._sign

        target, target_name = self._target(bar.close, risk)
        if target is None or not self._better(target, bar.close):
            return None
        return SweepEntry(
            direction=SignalDirection.BUY if self._long else SignalDirection.SELL,
            time=bar.timestamp, price=bar.close, stop=stop, target=target,
            level_name=self._level[0], level_price=self._level[1],
            sweep_extreme=self._sweep_extreme, leg_high=self._leg_high, target_name=target_name,
        )

    def _target(self, entry: float, risk: float) -> tuple[float | None, str]:
        if self._cfg.target_mode is TargetMode.FIXED_R:
            return entry + self._sign * self._cfg.target_r * risk, f"{self._cfg.target_r}R"
        ahead = [(n, p) for n, p in self._levels.target_side(long=self._long)
                 if self._better(p, entry)]
        if ahead:
            nearest = min(ahead, key=lambda kv: (kv[1] - entry) * self._sign)
            return nearest[1], nearest[0]
        # Every marked level is already behind price. The leg's own extreme is the last
        # pool of resting liquidity still ahead, so it stands in rather than the day
        # being dropped -- dropping it would quietly select for days that trended less.
        assert self._leg_high is not None
        return (self._leg_high, "LEG") if self._better(self._leg_high, entry) else (None, "")


def setup_id(symbol: str, entry: SweepEntry) -> str:
    return f"{STRATEGY_TAG}_{symbol}_{entry.time.astimezone(NY):%Y%m%d}_{entry.direction.name}"
