"""Gold AMD: sweep of the New York opening range, 15m FVG zone, 5m structure shift, 2R.

Accumulation  the 09:30-10:00 New York range of the day.
Manipulation  a 15m bar takes one side of that range AGAINST the 4H bias, and a 15m bar closes back
              inside within `close_back_bars`.
Distribution  a 15m fair-value gap in the bias direction forms; price returns to it; the first 5m
              close beyond the latest 5m swing is a market entry. Stop beyond the pullback extreme
              (plus the spread), target `tp_r` R, flat at 15:55 New York.

Researched 2026-09-30 / 2026-10-01 on gold only (it lost on every index and FX pair tried): CFI
2024-01..2026-09 74 trades, PF 1.99, +25.1R, max drawdown 4.1R; FundingPips 2025-03..2026-09 33
trades, PF 2.90, +18.1R. 128 runs with the feed shifted by 0.15-0.30 points per bar were all
profitable (mean +14..+20R). Known weak spots, which is why this is a PAPER bot:
  - ~83% of CFI profit came from longs in a gold bull market (trend dependence is untested);
  - the best of ~100 symbol x variant cells, so the number is optimistic by construction;
  - about two trades a month: twenty to thirty live trades take most of a year.

One code path: scripts and the live bot both call find_entry(), so the backtest and the bot cannot
drift. Every level is fixed from bars that had closed -- incomplete 4H/15m/5m buckets are dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from core.models import Bar, SignalDirection, Timeframe
from strategy.models import TradeSetup

NY = ZoneInfo("America/New_York")
SETUP_TAG = "setup_amd"  # setup ids start with it; MT5 keeps 29 comment chars, this id is 20


@dataclass(frozen=True)
class GoldAmdConfig:
    tp_r: float = 2.0
    min_fvg: float = 0.5           # a gap narrower than this (price points) is feed noise, not a zone
    spread_floor: float = 0.0      # MT5 bars store the minute's MINIMUM spread; floor it with a live one
    range_start: time = time(9, 30)
    range_end: time = time(10, 0)
    hunt_end: time = time(14, 0)   # the sweep must start before this (New York)
    entry_deadline: time = time(15, 0)
    flat_at: time = time(15, 55)
    close_back_bars: int = 4       # 15m bars within which a sweep must close back inside the range
    fvg_bars: int = 8              # 15m bars after the close-back in which the gap may form
    swing_lookback_s: int = 3600   # 5m swings older than this before the zone touch do not count


@dataclass(frozen=True)
class Frame:
    """OHLC buckets of one size. `to`/`tc` are open/close epoch seconds."""

    to: np.ndarray
    tc: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray


@dataclass(frozen=True)
class Series:
    """M1 arrays plus everything derived from them that find_entry() reads."""

    ts: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    sp: np.ndarray             # max(bar spread, spread_floor)
    floor: float
    f15: Frame
    f5: Frame
    bias_close: np.ndarray
    bias_val: np.ndarray


@dataclass(frozen=True)
class AmdEntry:
    direction: int             # +1 long, -1 short
    entry_time: int            # epoch seconds the market order is due
    entry: float
    stop: float
    index: int                 # M1 index of the entry bar; len(ts) while that bar has not formed yet


@dataclass(frozen=True)
class AmdScan:
    entry: AmdEntry | None
    reason: str                # where the day's funnel stopped, for the log


def build_frame(ts: np.ndarray, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, minutes: int,
                complete_until: int | None = None) -> Frame:
    """Buckets M1 bars into `minutes`-minute bars; with `complete_until`, drops buckets not yet closed."""
    df = pd.DataFrame({"b": ts // (minutes * 60), "o": o, "h": h, "l": l, "c": c})
    g = df.groupby("b", sort=True)
    out = pd.DataFrame({"o": g["o"].first(), "h": g["h"].max(), "l": g["l"].min(), "c": g["c"].last()})
    t_open = out.index.to_numpy(dtype=np.int64) * minutes * 60
    t_close = t_open + minutes * 60
    keep = np.ones(len(t_open), dtype=bool) if complete_until is None else t_close <= complete_until
    return Frame(to=t_open[keep], tc=t_close[keep], o=out["o"].to_numpy()[keep], h=out["h"].to_numpy()[keep],
                 l=out["l"].to_numpy()[keep], c=out["c"].to_numpy()[keep])


def bias_series(f4: Frame) -> tuple[np.ndarray, np.ndarray]:
    """(close time, bias) of every 4H bar: the side of the last 4H close through a confirmed swing.

    A swing is confirmed by the two 4H bars after it. +1 long, -1 short, 0 until the first break.
    """
    n = len(f4.c)
    bias = np.zeros(n, dtype=np.int8)
    swing_high = swing_low = None
    state = 0
    H, L, C = f4.h, f4.l, f4.c
    for k in range(n):
        j = k - 2
        if j >= 2:
            if H[j] > max(H[j - 2], H[j - 1], H[j + 1], H[j + 2]):
                swing_high = H[j]
            if L[j] < min(L[j - 2], L[j - 1], L[j + 1], L[j + 2]):
                swing_low = L[j]
        if swing_high is not None and C[k] > swing_high:
            state, swing_high = 1, None
        elif swing_low is not None and C[k] < swing_low:
            state, swing_low = -1, None
        bias[k] = state
    return f4.tc, bias


def build_series(ts: np.ndarray, o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray,
                 spread: np.ndarray, config: GoldAmdConfig, *, complete_until: int | None = None) -> Series:
    """Derives the frames and the bias from closed M1 bars (epoch-second `ts`, price-unit `spread`)."""
    if complete_until is None:
        complete_until = int(ts[-1]) + 60
    f4 = build_frame(ts, o, h, l, c, 240, complete_until)
    b_close, b_val = bias_series(f4)
    return Series(ts=ts, o=o, h=h, l=l, c=c, sp=np.maximum(spread, config.spread_floor), floor=config.spread_floor,
                  f15=build_frame(ts, o, h, l, c, 15, complete_until), f5=build_frame(ts, o, h, l, c, 5, complete_until),
                  bias_close=b_close, bias_val=b_val)


@dataclass(frozen=True)
class SessionTimes:
    range_start: int
    range_end: int
    hunt_end: int
    deadline: int
    flat: int


def session_times(day: date, config: GoldAmdConfig) -> SessionTimes:
    """The day's New York session boundaries as epoch seconds (DST-aware)."""
    def at(t: time) -> int:
        return int(datetime.combine(day, t, tzinfo=NY).timestamp())

    return SessionTimes(at(config.range_start), at(config.range_end), at(config.hunt_end),
                        at(config.entry_deadline), at(config.flat_at))


def _bias_at(s: Series, t: int) -> int:
    k = int(np.searchsorted(s.bias_close, t, side="right")) - 1
    return int(s.bias_val[k]) if k >= 0 else 0


def _mss_entry(s: Series, i_start: int, d: int, z_lo: float, z_hi: float, ext_stop: float,
               t_deadline: int, config: GoldAmdConfig, allow_pending: bool) -> tuple[AmdEntry | None, str]:
    """Back into the zone, then a 5m close through the latest 5m swing: a market entry."""
    ts, o, h, l, sp, f5 = s.ts, s.o, s.h, s.l, s.sp, s.f5
    n = len(ts)
    i = i_start
    touched = None
    while i < n and ts[i] < t_deadline:
        if d == 1:
            if l[i] <= ext_stop:
                return None, "stopped_before_zone"
            if l[i] <= z_hi:
                touched = i
                break
        else:
            if h[i] + sp[i] >= ext_stop:
                return None, "stopped_before_zone"
            if h[i] >= z_lo:
                touched = i
                break
        i += 1
    if touched is None:
        return None, "waiting_for_zone"
    t_touch = int(ts[touched])
    k0 = int(np.searchsorted(f5.tc, t_touch, side="left"))
    swing = None
    retrace = l[touched] if d == 1 else h[touched]
    for k in range(max(k0, 2), len(f5.tc)):
        if f5.tc[k] >= t_deadline:
            return None, "deadline_before_mss"
        if d == 1:
            retrace = min(retrace, f5.l[k])
            if retrace <= ext_stop:
                return None, "stopped_before_mss"
            if f5.h[k - 1] > f5.h[k - 2] and f5.h[k - 1] > f5.h[k] and f5.to[k - 1] >= t_touch - config.swing_lookback_s:
                swing = f5.h[k - 1]
            broke = swing is not None and f5.c[k] > swing
        else:
            retrace = max(retrace, f5.h[k])
            if retrace + s.floor >= ext_stop:
                return None, "stopped_before_mss"
            if f5.l[k - 1] < f5.l[k - 2] and f5.l[k - 1] < f5.l[k] and f5.to[k - 1] >= t_touch - config.swing_lookback_s:
                swing = f5.l[k - 1]
            broke = swing is not None and f5.c[k] < swing
        if not broke:
            continue
        i_e = int(np.searchsorted(ts, f5.tc[k], side="left"))
        if i_e >= n:
            # the 5m bar has just closed and the M1 bar that opens the entry is not in the data yet
            if not allow_pending:
                return None, "entry_bar_missing"
            t_entry, base, spr = int(f5.tc[k]), s.c[n - 1], sp[n - 1]
        else:
            t_entry, base, spr = int(ts[i_e]), o[i_e], sp[i_e]
        if t_entry >= t_deadline:
            return None, "deadline_before_mss"
        if d == 1:
            entry, stop = base + spr, retrace - spr
            bad = entry - stop <= 3 * spr
        else:
            entry, stop = base, retrace + 2 * spr
            bad = stop - entry <= 3 * spr
        if bad:
            return None, "stop_too_tight"
        return AmdEntry(d, t_entry, float(entry), float(stop), i_e), "signal"
    return None, "waiting_for_mss"


def find_entry(s: Series, times: SessionTimes, config: GoldAmdConfig, *, allow_pending: bool = False) -> AmdScan:
    """The day's one setup, if its market entry has triggered in the data given. Pure and stateless."""
    ts, h, l, f15 = s.ts, s.h, s.l, s.f15
    a, b = int(np.searchsorted(ts, times.range_start)), int(np.searchsorted(ts, times.range_end))
    if b - a < 15:
        return AmdScan(None, "range_not_ready")
    rh, rl = h[a:b].max(), l[a:b].min()
    j0 = int(np.searchsorted(f15.to, times.range_end))
    j1 = int(np.searchsorted(f15.tc, times.hunt_end, side="right"))
    d, jb = 0, None
    for j in range(j0, j1):
        up, dn = f15.h[j] > rh, f15.l[j] < rl
        if up or dn:
            d, jb = (-1 if up else 1), j      # a sweep of the highs sets up a short
            break
    if jb is None:
        return AmdScan(None, "no_sweep")
    if _bias_at(s, int(f15.tc[jb])) != d:
        return AmdScan(None, "bias_against_sweep")
    level = rh if d == -1 else rl
    k = next((q for q in range(jb, min(jb + config.close_back_bars, j1))
              if ((f15.c[q] < level) if d == -1 else (f15.c[q] > level))), None)
    if k is None:
        return AmdScan(None, "no_close_back")
    fvg = None
    for i in range(max(k, jb + 2), min(k + config.fvg_bars + 1, j1)):
        if d == -1 and f15.l[i - 2] > f15.h[i] and f15.l[i - 2] - f15.h[i] >= config.min_fvg:
            fvg = (i, f15.h[i], f15.l[i - 2])
            break
        if d == 1 and f15.h[i - 2] < f15.l[i] and f15.l[i] - f15.h[i - 2] >= config.min_fvg:
            fvg = (i, f15.h[i - 2], f15.l[i])
            break
    if fvg is None:
        return AmdScan(None, "no_fvg")
    i_f, z_lo, z_hi = fvg
    t0 = int(f15.tc[i_f])
    i_m = int(np.searchsorted(ts, t0, side="left"))
    if t0 >= times.deadline or i_m >= len(ts):
        return AmdScan(None, "waiting_for_zone" if t0 < times.deadline else "deadline_before_mss")
    spr = s.sp[i_m]
    stop = f15.h[jb:i_f + 1].max() + 2 * spr if d == -1 else f15.l[jb:i_f + 1].min() - spr
    entry, reason = _mss_entry(s, i_m, d, z_lo, z_hi, stop, times.deadline, config, allow_pending)
    return AmdScan(entry, reason)


class GoldAmdStrategy:
    """The live face of find_entry(): closed M1 bars in, a TradeSetup out while its entry is fresh."""

    def __init__(self, config: GoldAmdConfig | None = None) -> None:
        self.config = config if config is not None else GoldAmdConfig()

    def find_setup(self, bars: list[Bar], symbol: str, now: datetime,
                   grace_seconds: int = 240) -> tuple[TradeSetup | None, str, int | None]:
        """(setup, reason, seconds since the entry was due) for the New York day of `now`.

        `setup` is None unless today's entry triggered no more than `grace_seconds` ago: a later
        poll would be a different, worse entry than the one researched.
        """
        if not bars:
            return None, "no_bars", None
        now_s = int(now.timestamp())
        bars = [b for b in bars if int(b.timestamp.timestamp()) + 60 <= now_s]   # closed bars only
        ts = np.array([int(b.timestamp.timestamp()) for b in bars], dtype=np.int64)
        if len(ts) < 500:
            return None, "not_enough_history", None
        o, h, l, c = (np.array([getattr(b, f) for b in bars], dtype=float) for f in ("open", "high", "low", "close"))
        spread = np.array([b.spread for b in bars], dtype=float)
        series = build_series(ts, o, h, l, c, spread, self.config)
        day = now.astimezone(NY).date()
        scan = find_entry(series, session_times(day, self.config), self.config, allow_pending=True)
        if scan.entry is None:
            return None, scan.reason, None
        e = scan.entry
        age = now_s - e.entry_time
        if age > grace_seconds:
            return None, "signal_expired", age
        risk = abs(e.entry - e.stop)
        long = e.direction == 1
        target = e.entry + e.direction * self.config.tp_r * risk
        setup = TradeSetup(
            setup_id=f"{SETUP_TAG}_{day:%Y%m%d}_{'L' if long else 'S'}",
            symbol=symbol, timeframe=Timeframe.M5,
            direction=SignalDirection.BUY if long else SignalDirection.SELL,
            entry_zone=(round(e.entry, 5), round(e.entry, 5)),
            stop_zone=(round(e.stop, 5), round(e.stop, 5)),
            target_zone=(round(target, 5), round(target, 5)),
            confidence_score=1.0,
            confluence=[f"NY opening-range sweep against the 4H bias, 15m FVG zone, 5m MSS @ {e.entry:.2f}"],
            trigger_reason="gold_amd",
            invalidations=[f"stop {e.stop:.2f}"],
            related_structure_break=None, related_order_block=None, related_fvg=None,
            timestamp=datetime.fromtimestamp(e.entry_time, UTC),
            strategy_name="GoldAmd",
        )
        return setup, "signal", age
