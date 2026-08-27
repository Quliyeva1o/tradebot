"""Lookahead guard for the HTF-bias backtest scripts.

Regression coverage for a severe bug found during the 2026-08-27
production-readiness audit: both scripts derive a Daily Bias from 1H bars
resampled with label="left", closed="left" -- so the bar covering
[09:00, 10:00) is labelled "09:00" even though its high/low/close are only
known at 09:59. Forward-filling that bias straight from the 09:00 label let
a 09:05 execution bar trade on 09:59 information.

Measured impact before the fix (real MT5 data, 6 years):
    Order Flow XAUUSD 5m : PF 1.63 (+76.6R) -> PF 0.99 (-1.4R)
    Order Flow NAS100 15m: PF 1.70 (+42.7R) -> PF 0.94 (-5.1R)
i.e. the entire apparent edge WAS the leak.

TEST DESIGN NOTES (two dead ends, recorded so they are not retried):
  1. "Truncate history at T, check bias at T is unchanged" is VACUOUS -- a
     truncated final hour yields NaN pivots and a neutral vote either way,
     so it passed even with the bug deliberately reintroduced.
  2. A seeded random-walk fixture is also VACUOUS -- it never produces a
     non-neutral bias (the vote threshold needs several indicators to
     agree), so every comparison comes out 0 == 0.
What has teeth is REAL price data plus a future-perturbation: scramble
every bar strictly after T and assert the bias series up to and including T
is bit-identical. If a future bar can move a past bias value, that is a
leak by definition.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

import scripts.order_flow_bias_backtest as ofb
import scripts.po3_backtest as po3
from scripts.backtest_common import BROKER_TZ, NY
DATA = Path("data/history/XAUUSD_M1.csv")
MAX_ROWS = 90_000  # ~60 calendar days of M1: enough to warm up the daily/1H/15M votes, fast to load


def _load_real_slice(path: Path, max_rows: int) -> pd.DataFrame:
    """Same parsing as the modules' own load_m1(), but stops early so the
    test stays fast (the full file is millions of rows).
    """
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f)):
            if i >= max_rows:
                break
            ts = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BROKER_TZ).astimezone(NY)
            rows.append((ts, float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), float(row["volume"])))
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    return df.set_index("ts").sort_index()


@pytest.fixture(scope="module")
def real_m1() -> pd.DataFrame:
    if not DATA.exists():
        pytest.skip(f"{DATA} not present (gitignored market data); lookahead guard needs real prices")
    return _load_real_slice(DATA, MAX_ROWS)


def _probe_and_perturbed(module, m1: pd.DataFrame):
    """Picks a probe EARLY inside an hour (minute :05 -- where the old leak
    bit hardest), then rewrites every bar strictly after it.
    """
    base = module.compute_daily_bias(m1, module.compute_daily_levels(m1))
    # Choose a probe where the bias is actually directional, so the
    # comparison cannot pass vacuously.
    candidates = base.index[(base != 0).to_numpy() & (base.index.minute == 5)]
    if len(candidates) == 0:
        pytest.skip("no directional-bias probe found in this data slice")
    probe = candidates[len(candidates) // 2]

    perturbed_m1 = m1.copy()
    future = perturbed_m1.index > probe
    perturbed_m1.loc[future, ["open", "high", "low", "close"]] += 250.0
    perturbed_m1.loc[future, "volume"] *= 37.0
    perturbed = module.compute_daily_bias(perturbed_m1, module.compute_daily_levels(perturbed_m1))
    return probe, base, perturbed


@pytest.mark.parametrize("module", [ofb, po3], ids=["order_flow_bias", "po3"])
def test_daily_bias_is_unaffected_by_future_bars(module, real_m1: pd.DataFrame) -> None:
    """No bias value at or before T may change when only bars after T change."""
    probe, base, perturbed = _probe_and_perturbed(module, real_m1)
    past = base.index <= probe
    mismatches = int((base[past].to_numpy() != perturbed[past].to_numpy()).sum())
    assert mismatches == 0, (
        f"{module.__name__}.compute_daily_bias LEAKS FUTURE DATA: {mismatches} bias values at or "
        f"before {probe} changed when only post-{probe} bars were modified "
        f"(bias at probe: {float(base.loc[probe])} -> {float(perturbed.loc[probe])})"
    )


@pytest.mark.parametrize("module", [ofb, po3], ids=["order_flow_bias", "po3"])
def test_perturbation_is_strong_enough_to_matter(module, real_m1: pd.DataFrame) -> None:
    """Guards the guard: the perturbation must visibly move the bias AFTER
    the probe, otherwise the assertion above could pass vacuously.
    """
    probe, base, perturbed = _probe_and_perturbed(module, real_m1)
    future = base.index > probe
    assert (base[future].to_numpy() != perturbed[future].to_numpy()).any(), (
        "future-perturbation did not change the bias even after the probe -- fixture too weak"
    )
