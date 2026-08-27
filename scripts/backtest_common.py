"""Shared primitives for the M1-driven strategy backtests.

Extracted from `order_flow_bias_backtest.py`, `order_flow_daily_bias_backtest.py`
and `po3_backtest.py`, which each carried a byte-identical private copy of the
data loader, the resampler, ATR and the pivot detector. Three copies meant a
correctness fix had to be applied three times by hand -- and the HTF-bias
lookahead fix (see `htf_bias_known_from` below) very nearly was not.

DELIBERATE NON-GOAL -- session VWAP is NOT here. The two Order Flow scripts
define it differently and both definitions are intentional:

  * `order_flow_bias_backtest.compute_daily_levels` weights **close** price
    (`close * volume`) and back-fills empty sessions with the close.
  * `order_flow_daily_bias_backtest.compute_session_vwap` weights **typical**
    price (`(high + low + close) / 3`) and leaves empty sessions NaN.

Unifying them would silently move both strategies' results, so they stay in
their own modules until someone deliberately decides which one is correct.
"""

from __future__ import annotations

import csv
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

# All backtests work in New York time (the session all three strategies are
# defined against); the broker's CSV timestamps are Europe/Bucharest wall
# clock, so load_m1 converts once at load and nothing downstream re-localises.
NY = ZoneInfo("America/New_York")
BROKER_TZ = ZoneInfo("Europe/Bucharest")


# --------------------------------------------------------------------------
# Data loading / resampling
# --------------------------------------------------------------------------

def load_m1(path: str) -> pd.DataFrame:
    """Loads a broker M1 CSV, converting broker-local timestamps to NY time.

    Also usable for any other bar size (the parser only cares about the
    OHLCV+time columns) -- `order_flow_daily_bias_backtest.run_backtest_native_h1`
    relies on that to feed it a native H1 file.
    """
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            naive = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            broker_local = naive.replace(tzinfo=BROKER_TZ)
            ny_ts = broker_local.astimezone(NY)
            rows.append(
                (ny_ts, float(row["open"]), float(row["high"]), float(row["low"]),
                 float(row["close"]), float(row["volume"]))
            )
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    return df.set_index("ts").sort_index()


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Aggregates to `minutes` bars, labelled by the bar's START time.

    NOTE the labelling: a bar labelled 09:00 covers [09:00, 10:00) and its
    high/low/close are only known at 09:59. Anything that samples a
    HIGHER-timeframe bar from a lower-timeframe one MUST go through
    `htf_bias_known_from` first -- see the warning on that function.
    """
    rule = f"{minutes}min"
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["open"])


# --------------------------------------------------------------------------
# Indicators
# --------------------------------------------------------------------------

def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1 / period, adjust=False).mean()


def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return wilder_smooth(tr, period)


def compute_pivots(
    df: pd.DataFrame, left: int, right: int | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Non-repainting pivot high/low (ta.pivothigh / ta.pivotlow semantics).

    The pivot's VALUE is written at the CONFIRMATION bar (`pivot + right`),
    not at the pivot itself, because that is the first bar at which the pivot
    is knowable. Reading `ph[i]` therefore never uses future information.

    `right` defaults to `left` (a symmetric half-window), which is how all
    current callers use it.
    """
    if right is None:
        right = left
    n = len(df)
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    ph, pl = np.full(n, np.nan), np.full(n, np.nan)
    for i in range(left, n - right):
        wh = highs[i - left : i + right + 1]
        if highs[i] == wh.max():
            ph[i + right] = highs[i]
        wl = lows[i - left : i + right + 1]
        if lows[i] == wl.min():
            pl[i + right] = lows[i]
    return ph, pl


def compute_pivots_series(
    df: pd.DataFrame, left: int, right: int | None = None
) -> tuple[pd.Series, pd.Series]:
    """`compute_pivots` with the result re-attached to `df`'s index."""
    ph, pl = compute_pivots(df, left, right)
    return pd.Series(ph, index=df.index), pd.Series(pl, index=df.index)


# --------------------------------------------------------------------------
# Higher-timeframe bias: the lookahead guard
# --------------------------------------------------------------------------

def htf_bias_known_from(bias_by_bar_start: pd.Series, htf_minutes: int) -> pd.Series:
    """Re-stamps an HTF series from bar-START labels to KNOWN-FROM labels.

    CRITICAL -- THIS IS A CORRECTNESS FIX, NOT A STYLE CHOICE. NEVER REMOVE IT.

    `resample(label="left", closed="left")` labels the 1H bar covering
    [09:00, 10:00) as "09:00", but its high/low/close are only known at 09:59.
    Sampling that bias straight from the "09:00" label lets a 09:05 execution
    bar trade on 09:59 information. Measured on real data, 24.2% of 5m bars
    carried a bias they could not yet have known.

    That leak WAS this strategy family's entire apparent edge:

        Order Flow XAUUSD 5m : PF 1.63 (+76.6R)  ->  PF 0.99 (-1.4R)
        Order Flow NAS100 15m: PF 1.70 (+42.7R)  ->  PF 0.94 (-5.1R)

    Moving each label forward by one full HTF period means an execution bar
    can only ever act on an HTF bar that has actually CLOSED.

    Regression test: `tests/test_backtest_lookahead.py` (perturbs the future
    and asserts no past bias value moves). Two earlier formulations of that
    test were vacuous and are documented in the file so they are not retried.

    Args:
        bias_by_bar_start: HTF-derived series indexed by HTF bar START time.
        htf_minutes: the HTF bar length in minutes (60 for a 1H bias).

    Returns:
        The same values, re-indexed to the time each one first became known.
    """
    return bias_by_bar_start.shift(1, freq=f"{htf_minutes}min")


def htf_bias_to_index(
    bias_by_bar_start: pd.Series, htf_minutes: int, target_index: pd.DatetimeIndex
) -> pd.Series:
    """`htf_bias_known_from` + forward-fill onto an execution-TF index.

    Bars before the first closed HTF bar get 0 (Neutral) rather than NaN.
    """
    return (
        htf_bias_known_from(bias_by_bar_start, htf_minutes)
        .reindex(target_index, method="ffill")
        .fillna(0)
    )


def bias_lookup(bias_known_from: pd.Series, ts: pd.Timestamp) -> int | None:
    """Most recent bias value already KNOWN at `ts`.

    The alternative to `htf_bias_to_index` for callers that sample the bias
    per-bar inside a loop instead of materialising a full aligned series.
    `side="right"` is what makes a value stamped exactly at `ts` usable --
    `htf_bias_known_from` has already moved each label to its close time, so
    a label equal to `ts` is genuinely available.

    Returns None when `ts` precedes the first closed HTF bar.
    """
    pos = bias_known_from.index.searchsorted(ts, side="right") - 1
    if pos < 0:
        return None
    return int(bias_known_from.iloc[pos])
