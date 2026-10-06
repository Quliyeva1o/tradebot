"""M1 history -> M5 frames in New York wall-clock minutes, plus the daily levels the ICT models use.

Everything is bid OHLC. `wall` is New York wall-clock minutes since 1970-01-01 (day*1440 + minute of
day), so "03:00 NY on day D" is simply D*1440 + 180 and survives both brokers' server-clock shifts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from backtest.live_replay.market import load_m1

HIST = Path("data/history")
START = datetime(2021, 1, 1, tzinfo=timezone.utc)

# symbol -> (folder, file symbol, spread floor in price units, SMT partner (folder, file symbol))
SYMBOLS = {
    "XAUUSD": ("cfi", "XAUUSD_", 0.15, ("cfi_portfolio", "XAGUSD_")),
    "US100": ("cfi", "US100_Spot", 0.8, ("cfi_portfolio", "US500_SPOT")),
    "EURUSD": ("cfi", "EURUSD_", 0.0001, ("cfi", "GBPUSD_")),
    "GBPUSD": ("cfi", "GBPUSD_", 0.00012, ("cfi", "EURUSD_")),
    # added 2026-10-06: the other majors the scan covers; partners are the closest same-direction pair on file
    "USDJPY": ("cfi_portfolio", "USDJPY_", 0.004, ("cfi_portfolio", "GBPJPY_")),
    "USDCAD": ("cfi_portfolio", "USDCAD_", 0.00006, ("cfi_portfolio", "USDJPY_")),
    "AUDUSD": ("cfi_portfolio", "AUDUSD_", 0.00004, ("cfi_portfolio", "EURUSD_")),
    # index out-of-sample for the US100 Order Block A+ check
    "US500": ("cfi_portfolio", "US500_SPOT", 0.5, ("cfi", "US100_Spot")),
    "US30": ("cfi", "US30_SPOT", 2.0, ("cfi_portfolio", "US500_SPOT")),
}
BASE4 = ("XAUUSD", "US100", "EURUSD", "GBPUSD")


@dataclass
class Frame:
    ts: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    sp: np.ndarray
    wall: np.ndarray = field(init=False)
    day: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        ny = pd.DatetimeIndex(pd.to_datetime(self.ts, unit="s", utc=True)).tz_convert("America/New_York")
        self.wall = ny.tz_localize(None).values.astype("datetime64[m]").astype(np.int64)
        self.day = self.wall // 1440

    def __len__(self) -> int:
        return len(self.ts)


def _load(folder: str, name: str) -> Frame:
    m = load_m1(name, HIST / folder, start=START)
    return Frame(m.ts, m.open, m.high, m.low, m.close, m.spread.astype(float))


def to_m5(f: Frame, floor: float) -> Frame:
    b = f.ts - f.ts % 300
    st = np.flatnonzero(np.r_[True, b[1:] != b[:-1]])
    en = np.r_[st[1:], len(b)] - 1
    # the bar's spread is the widest of its minutes: a limit order fills on whichever minute touches it
    sp = np.maximum.reduceat(f.sp, st)
    return Frame(b[st], f.o[st], np.maximum.reduceat(f.h, st), np.minimum.reduceat(f.l, st), f.c[en],
                 np.maximum(sp, floor))


def pivots(x: np.ndarray, n: int, high: bool) -> np.ndarray:
    """Fractal pivots: strictly beyond the n bars before, at least as far as the n bars after."""
    s = pd.Series(x)
    if high:
        left = s.rolling(n).max().shift(1)
        right = s[::-1].rolling(n).max().shift(1)[::-1]
        out = (s > left) & (s >= right)
    else:
        left = s.rolling(n).min().shift(1)
        right = s[::-1].rolling(n).min().shift(1)[::-1]
        out = (s < left) & (s <= right)
    return out.fillna(False).to_numpy()


@dataclass
class Sym:
    name: str
    floor: float
    m1: Frame
    m5: Frame
    pair_h: np.ndarray  # SMT partner's M5 high/low aligned to m5 (NaN where it has no bar)
    pair_l: np.ndarray
    avg_body: np.ndarray = field(init=False)
    avg_rng: np.ndarray = field(init=False)
    days: dict = field(init=False)     # day -> (first m5 idx, last m5 idx + 1)
    tdays: list = field(init=False)    # Mon-Fri days with a full session, ascending
    daily: dict = field(init=False)    # day -> (high, low, close)
    tindex: dict = field(init=False)   # day -> position in tdays

    def __post_init__(self) -> None:
        f = self.m5
        body = np.abs(f.c - f.o)
        rng = f.h - f.l
        self.avg_body = self._trail(body)
        self.avg_rng = self._trail(rng)
        d = f.day
        starts = np.flatnonzero(np.r_[True, d[1:] != d[:-1]])
        ends = np.r_[starts[1:], len(d)]
        self.days = {int(d[s]): (int(s), int(e)) for s, e in zip(starts, ends)}
        self.daily = {}
        for day, (s, e) in self.days.items():
            self.daily[day] = (float(f.h[s:e].max()), float(f.l[s:e].min()), float(f.c[e - 1]))
        self.tdays = [x for x, (s, e) in sorted(self.days.items()) if (x + 3) % 7 < 5 and e - s >= 100]
        self.tindex = {x: i for i, x in enumerate(self.tdays)}

    @staticmethod
    def _trail(x: np.ndarray, n: int = 20) -> np.ndarray:
        c = np.r_[0.0, np.cumsum(x)]
        out = np.full(len(x), np.nan)
        out[n:] = (c[n:len(x)] - c[:len(x) - n]) / n  # mean of the n bars BEFORE each bar
        return out

    def win(self, day: int, a: int, b: int) -> tuple[int, int]:
        """M5 index range of New York wall minutes [day*1440+a, day*1440+b)."""
        w = self.m5.wall
        return int(np.searchsorted(w, day * 1440 + a)), int(np.searchsorted(w, day * 1440 + b))

    def hl(self, day: int, a: int, b: int):
        i, j = self.win(day, a, b)
        if j - i < 3:
            return None
        return float(self.m5.h[i:j].max()), float(self.m5.l[i:j].min())

    def levels(self, day: int) -> dict | None:
        """Levels a trader has at 00:00 NY on `day`; None if there is no usable history."""
        k = self.tindex.get(day)
        if k is None or k < 21:
            return None
        pd1, pd2 = self.tdays[k - 1], self.tdays[k - 2]
        h1, l1, c1 = self.daily[pd1]
        h2, l2, _ = self.daily[pd2]
        bias = 1 if c1 > h2 else -1 if c1 < l2 else 0
        wk = (day + 3) // 7
        pw = [x for x in self.tdays[max(0, k - 12):k] if (x + 3) // 7 == wk - 1]
        lv = {"pdh": h1, "pdl": l1, "bias": bias, "pd_close": c1}
        if pw:
            lv["pwh"] = max(self.daily[x][0] for x in pw)
            lv["pwl"] = min(self.daily[x][1] for x in pw)
        w20 = self.tdays[k - 20:k]
        lv["d20h"] = max(self.daily[x][0] for x in w20)
        lv["d20l"] = min(self.daily[x][1] for x in w20)
        lv["adr"] = float(np.mean([self.daily[x][0] - self.daily[x][1] for x in w20]))
        asia = self.hl(day - 1, 20 * 60, 24 * 60)
        if asia:
            lv["asiah"], lv["asial"] = asia
        lon = self.hl(day, 2 * 60, 7 * 60)
        if lon:
            lv["lonh"], lv["lonl"] = lon
        cb = self.hl(self.tdays[k - 1], 14 * 60, 20 * 60)
        if cb:
            lv["cbdrh"], lv["cbdrl"] = cb
        i, j = self.win(day, 0, 5)
        if j > i:
            lv["mo"] = float(self.m5.o[i])
        return lv


def load_symbol(name: str) -> Sym:
    folder, fname, floor, (pf, pn) = SYMBOLS[name]
    m1 = _load(folder, fname)
    m1.sp = np.maximum(m1.sp, floor)
    m5 = to_m5(m1, floor)
    pm5 = to_m5(_load(pf, pn), 0.0)
    pos = np.searchsorted(pm5.ts, m5.ts)
    pos_c = np.minimum(pos, len(pm5.ts) - 1)
    ok = pm5.ts[pos_c] == m5.ts
    ph = np.where(ok, pm5.h[pos_c], np.nan)
    pl = np.where(ok, pm5.l[pos_c], np.nan)
    return Sym(name, floor, m1, m5, ph, pl)
