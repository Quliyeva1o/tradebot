"""Finds the historical 5M windows whose MARKET STRUCTURE most resembles right now.

Not a candle-shape search. Two windows can trace nearly the same squiggle and be
structurally opposite -- one making higher highs into a bullish BOS, the other
sweeping equal highs and rolling over. So the score is dominated (65%) by a
structural feature vector (swing sequence, BOS/CHoCH, sweeps, displacement,
unfilled FVGs, premium/discount, compression, volatility and volume regime) and
only 35% by the normalized price path.

TWO THINGS MAKE OR BREAK THIS KIND OF STUDY, and both are enforced here:

1. NO LOOKAHEAD. Every feature for a window ending at bar i is computed from
   bars <= i only. Swing pivots are stamped at their CONFIRMATION bar
   (`compute_pivots`, left/right window), so a pivot is never "known" before the
   market could have known it. Outcomes read bars > i exclusively. The current
   window is built the same way as every historical one, so it is scored against
   them on equal terms.

2. A BASE RATE, ALWAYS. "60% of matches went up" is worth nothing if 60% of ALL
   windows went up. Every statistic here is printed beside the same statistic
   over the entire sample, and the only number that means anything is the
   difference. With ~470k 5M bars, a handful of "closest" matches is a tiny,
   self-selected sample -- the base-rate column is what keeps that honest.

Overlapping matches are collapsed (a good match's neighbours are also good
matches, and counting them separately would fake a sample size), and windows
adjacent to the present are excluded so "now" cannot match its own recent past.

Usage:
    python -m scripts.structure_match --symbol XAUUSD
    python -m scripts.structure_match --symbol NDX100 --window 60 --horizon 36 --top 5
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from scripts.backtest_common import NY, compute_atr, compute_pivots, load_m1, resample

DATA_DIR = Path("data/history/fundingpips")

PIVOT_LEFT = PIVOT_RIGHT = 2      # 5M fractal; a pivot is confirmed 2 bars later
DISPLACEMENT_ATR = 1.5            # bar range beyond this many ATR = displacement
SWEEP_EPS_ATR = 0.05              # wick must exceed the level by this much to count
EQUAL_EPS_ATR = 0.15              # two swings within this are "equal"
EXPANSION_ATR = 1.0               # |move| beyond this counts as the expansion


# --------------------------------------------------------------------------
# Series-wide primitives (computed once, sliced per window)
# --------------------------------------------------------------------------

def build_primitives(df: pd.DataFrame) -> dict:
    """Per-bar structural facts, each knowable at that bar's close."""
    h, l, c, o = (df[x].to_numpy(float) for x in ("high", "low", "close", "open"))
    v = df["volume"].to_numpy(float)
    n = len(df)

    atr = compute_atr(df, 14).to_numpy(float)
    atr = np.where(np.isfinite(atr) & (atr > 0), atr, np.nan)

    # Pivots land on their confirmation bar, so ph[i] is a high that became
    # knowable at i -- never before.
    ph, pl = compute_pivots(df, PIVOT_LEFT, PIVOT_RIGHT)

    rng = h - l
    body = np.abs(c - o)
    displacement = np.where(atr > 0, rng / atr, 0.0)

    # 3-bar FVG, detectable at bar i (uses i, i-1, i-2 only).
    fvg_up = np.zeros(n, bool)
    fvg_dn = np.zeros(n, bool)
    fvg_up[2:] = l[2:] > h[:-2]
    fvg_dn[2:] = h[2:] < l[:-2]

    vol_ma = pd.Series(v).rolling(50, min_periods=10).mean().to_numpy()
    vol_ratio = np.where(vol_ma > 0, v / vol_ma, 1.0)

    return dict(h=h, l=l, c=c, o=o, v=v, atr=atr, ph=ph, pl=pl,
                rng=rng, body=body, displacement=displacement,
                fvg_up=fvg_up, fvg_dn=fvg_dn, vol_ratio=vol_ratio, n=n)


def _swing_sequence(ph_w: np.ndarray, pl_w: np.ndarray) -> tuple[list, list, list]:
    """(highs, lows, labels) from a window's confirmed pivots, in order."""
    highs = [(i, ph_w[i]) for i in range(len(ph_w)) if np.isfinite(ph_w[i])]
    lows = [(i, pl_w[i]) for i in range(len(pl_w)) if np.isfinite(pl_w[i])]
    labels = []
    for k in range(1, len(highs)):
        labels.append(("HH", highs[k][0]) if highs[k][1] > highs[k - 1][1] else ("LH", highs[k][0]))
    for k in range(1, len(lows)):
        labels.append(("HL", lows[k][0]) if lows[k][1] > lows[k - 1][1] else ("LL", lows[k][0]))
    labels.sort(key=lambda x: x[1])
    return highs, lows, labels


def describe_window(P: dict, end: int, w: int) -> dict | None:
    """Full structural read of the window ending AT bar `end` (inclusive).

    Uses bars [end-w+1, end] and nothing after. Returns None if the window is
    incomplete or ATR is not yet warm.
    """
    s = end - w + 1
    if s < 0 or not np.isfinite(P["atr"][end]) or P["atr"][end] <= 0:
        return None

    a = P["atr"][end]
    h, l, c = P["h"][s:end + 1], P["l"][s:end + 1], P["c"][s:end + 1]
    ph_w, pl_w = P["ph"][s:end + 1], P["pl"][s:end + 1]
    highs, lows, labels = _swing_sequence(ph_w, pl_w)

    hi, lo = float(h.max()), float(l.min())
    span = max(hi - lo, 1e-9)
    pos_in_range = (float(c[-1]) - lo) / span          # 0 = discount low, 1 = premium high

    counts = {k: sum(1 for lab, _ in labels if lab == k) for k in ("HH", "HL", "LH", "LL")}
    bull_struct = counts["HH"] + counts["HL"]
    bear_struct = counts["LH"] + counts["LL"]
    trend = (bull_struct - bear_struct) / max(bull_struct + bear_struct, 1)

    # BOS / CHoCH: a close beyond the previous confirmed swing, read in order.
    bos_up = bos_dn = 0
    last_bos_dir = 0
    choch = 0
    dirn = 0
    prev_hi = prev_lo = None
    for i in range(len(c)):
        if np.isfinite(ph_w[i]):
            prev_hi = ph_w[i]
        if np.isfinite(pl_w[i]):
            prev_lo = pl_w[i]
        if prev_hi is not None and c[i] > prev_hi:
            bos_up += 1
            last_bos_dir = 1
            if dirn == -1:
                choch = 1          # break UP while structure was bearish
            dirn = 1
            prev_hi = None
        elif prev_lo is not None and c[i] < prev_lo:
            bos_dn += 1
            last_bos_dir = -1
            if dirn == 1:
                choch = -1         # break DOWN while structure was bullish
            dirn = -1
            prev_lo = None

    # Liquidity sweeps: wick takes a prior swing, close comes back inside.
    sweep_hi = sweep_lo = 0
    for k in range(1, len(highs)):
        i, lvl = highs[k]
        prior = max(x[1] for x in highs[:k])
        if h[i] > prior + SWEEP_EPS_ATR * a and c[i] < prior:
            sweep_hi += 1
    for k in range(1, len(lows)):
        i, lvl = lows[k]
        prior = min(x[1] for x in lows[:k])
        if l[i] < prior - SWEEP_EPS_ATR * a and c[i] > prior:
            sweep_lo += 1

    eq_hi = sum(1 for k in range(1, len(highs))
                if abs(highs[k][1] - highs[k - 1][1]) < EQUAL_EPS_ATR * a)
    eq_lo = sum(1 for k in range(1, len(lows))
                if abs(lows[k][1] - lows[k - 1][1]) < EQUAL_EPS_ATR * a)

    disp = P["displacement"][s:end + 1]
    disp_mask = disp > DISPLACEMENT_ATR
    disp_up = int(np.sum(disp_mask & (P["c"][s:end + 1] > P["o"][s:end + 1])))
    disp_dn = int(np.sum(disp_mask & (P["c"][s:end + 1] < P["o"][s:end + 1])))
    bars_since_disp = w - 1 - int(np.max(np.where(disp_mask)[0])) if disp_mask.any() else w

    # Unfilled FVGs: gap formed in-window and price has not traded back through it.
    fvg_open_up = fvg_open_dn = 0
    for i in range(2, len(c)):
        if P["fvg_up"][s + i]:
            top, bot = l[i], h[i - 2]
            if l[i:].min() > bot:
                fvg_open_up += 1
        if P["fvg_dn"][s + i]:
            top, bot = l[i - 2], h[i]
            if h[i:].max() < top:
                fvg_open_dn += 1

    half = w // 2
    compression = (float(np.max(h[half:]) - np.min(l[half:]))) / a   # recent-half range in ATR
    net_atr = (float(c[-1]) - float(c[0])) / a
    vol_regime = float(np.nanmean(P["vol_ratio"][s:end + 1]))
    atr_pctl = float(np.nanmean(P["atr"][max(0, end - 500):end + 1] <= a))

    return dict(
        end=end, hh=counts["HH"], hl=counts["HL"], lh=counts["LH"], ll=counts["LL"],
        trend=trend, bos_up=bos_up, bos_dn=bos_dn, last_bos_dir=last_bos_dir, choch=choch,
        sweep_hi=sweep_hi, sweep_lo=sweep_lo, eq_hi=eq_hi, eq_lo=eq_lo,
        disp_up=disp_up, disp_dn=disp_dn, bars_since_disp=bars_since_disp,
        fvg_up=fvg_open_up, fvg_dn=fvg_open_dn,
        pos_in_range=pos_in_range, compression=compression, net_atr=net_atr,
        range_atr=span / a, vol_regime=vol_regime, atr_pctl=atr_pctl,
        n_swings=len(highs) + len(lows), atr=a, hi=hi, lo=lo,
    )


# The structural vector and its weights. Weights encode the user's own priority
# order: structure sequence and BOS/CHoCH first, liquidity next, then
# displacement/FVG, then location, then volatility/volume.
FEATURES = [
    ("trend", 3.0), ("hh", 1.5), ("hl", 1.5), ("lh", 1.5), ("ll", 1.5),
    ("bos_up", 2.5), ("bos_dn", 2.5), ("last_bos_dir", 2.0), ("choch", 3.0),
    ("sweep_hi", 2.0), ("sweep_lo", 2.0), ("eq_hi", 1.0), ("eq_lo", 1.0),
    ("disp_up", 1.5), ("disp_dn", 1.5), ("bars_since_disp", 1.0),
    ("fvg_up", 1.2), ("fvg_dn", 1.2),
    ("pos_in_range", 2.5), ("compression", 1.5), ("net_atr", 2.0), ("range_atr", 1.5),
    ("vol_regime", 1.0), ("atr_pctl", 1.0),
]


def feature_matrix(rows: list[dict]) -> np.ndarray:
    return np.array([[r[k] for k, _ in FEATURES] for r in rows], float)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--window", type=int, default=60, help="5M bars in the structure window (60 = 5h)")
    ap.add_argument("--horizon", type=int, default=36, help="5M bars of forward outcome (36 = 3h)")
    ap.add_argument("--top", type=int, default=5, help="matches shown in full detail")
    ap.add_argument("--stat-n", type=int, default=50,
                    help="matches used for the STATISTICS. 5 is a headline, not a sample; "
                         "the direction split only starts separating from noise in the dozens.")
    ap.add_argument("--session-hours", type=float, default=2.0,
                    help="only match windows ending within this many hours of the current "
                         "time of day. 5M structure is session-bound: the same shape at 03:30 "
                         "Asian and 09:30 NY open are different animals, and ATR-normalising a "
                         "quiet hour makes the next session's ordinary move look enormous. "
                         "0 disables the filter.")
    ap.add_argument("--live-csv", default=None, help="M5 CSV with today's bars, appended to history")
    ap.add_argument("--out", default=None, help="directory for the chart PNGs")
    ap.add_argument("--levels", action="store_true",
                    help="grid-search TP/SL against the matched windows, walking each one "
                         "bar by bar to see which level is touched FIRST (SL wins ties inside "
                         "a bar). Endpoint MFE/MAE averages overstate what a real bracket "
                         "captures, because they ignore the stop that would have ended the trade.")
    args = ap.parse_args()

    w, H = args.window, args.horizon

    # ---- data: full M1 history resampled to 5M, plus today's live tail ----
    m1 = load_m1(str(DATA_DIR / f"{args.symbol}_M1.csv"))
    df = resample(m1, 5)
    df.index = df.index.tz_convert(NY)
    if args.live_csv:
        live = pd.read_csv(args.live_csv, parse_dates=["time"]).set_index("time")
        live.index = live.index.tz_convert(NY)
        df = pd.concat([df[df.index < live.index[0]], live[["open", "high", "low", "close", "volume"]]])
        df = df[~df.index.duplicated(keep="last")].sort_index()
    print(f"[data] {args.symbol} 5M: {len(df)} bar, {df.index[0]} -> {df.index[-1]}", file=sys.stderr)

    P = build_primitives(df)
    idx = df.index

    cur = describe_window(P, P["n"] - 1, w)
    if cur is None:
        raise SystemExit("Current window incomplete.")

    # ---- candidate windows: every bar with a full window AND a full horizon ----
    # Excluding the last (w + H) bars keeps "now" from matching its own recent
    # past, and guarantees every candidate has a real, complete outcome.
    cands = []
    for e in range(w + 20, P["n"] - H - w):
        d = describe_window(P, e, w)
        if d is not None:
            cands.append(d)
    print(f"[scan] {len(cands)} tam pencere qiymetlendirilir", file=sys.stderr)

    Fc = feature_matrix(cands)
    fcur = feature_matrix([cur])[0]
    wts = np.array([x for _, x in FEATURES], float)

    # Scale each feature by its own historical spread so no single one dominates.
    scale = np.where(Fc.std(axis=0) > 1e-9, Fc.std(axis=0), 1.0)
    struct_d = np.sqrt((((Fc - fcur) / scale) ** 2 * wts).sum(axis=1) / wts.sum())

    # Shape: path normalized by the window's own ATR -- scale-free across price
    # levels, but (unlike z-scoring) a quiet range still cannot match a trend.
    def path(end: int, a: float) -> np.ndarray:
        seg = P["c"][end - w + 1:end + 1]
        return (seg - seg[0]) / a

    pcur = path(P["n"] - 1, cur["atr"])
    paths = np.array([path(d["end"], d["atr"]) for d in cands])
    shape_d = np.sqrt(((paths - pcur) ** 2).mean(axis=1))

    struct_sim = 100 * np.exp(-struct_d)
    shape_sim = 100 * np.exp(-shape_d / 2.0)
    total = 0.65 * struct_sim + 0.35 * shape_sim

    # Session control. Intraday 5M structure is not session-free: the same
    # shape at 03:30 Asian and 09:30 NY open behave differently, and because
    # every outcome here is measured in ATR, a match anchored to a quiet hour
    # divides the next session's perfectly ordinary move by a tiny ATR and
    # reports it as enormous. Restricting matches to a similar time of day
    # removes that artifact rather than averaging over it.
    cur_tod = idx[-1].hour + idx[-1].minute / 60
    if args.session_hours > 0:
        tod = np.array([idx[d["end"]].hour + idx[d["end"]].minute / 60 for d in cands])
        gap = np.abs(tod - cur_tod)
        gap = np.minimum(gap, 24 - gap)                 # wrap around midnight
        in_session = gap <= args.session_hours
        total = np.where(in_session, total, -1.0)
        print(f"[session] cari saat {cur_tod:.2f} NY, +/-{args.session_hours}s pencerede "
              f"{int(in_session.sum())} namized qalir", file=sys.stderr)

    # ---- forward outcomes for every candidate (base rate + matches alike) ----
    ends = np.array([d["end"] for d in cands])
    atrs = np.array([d["atr"] for d in cands])
    c0 = P["c"][ends]
    net = np.array([(P["c"][e + H] - P["c"][e]) / a for e, a in zip(ends, atrs)])
    mfe = np.array([(P["h"][e + 1:e + H + 1].max() - P["c"][e]) / a for e, a in zip(ends, atrs)])
    mae = np.array([(P["l"][e + 1:e + H + 1].min() - P["c"][e]) / a for e, a in zip(ends, atrs)])

    def bars_to_expansion(e: int, a: float) -> int:
        seg = np.abs(P["c"][e + 1:e + H + 1] - P["c"][e]) / a
        hit = np.where(seg >= EXPANSION_ATR)[0]
        return int(hit[0]) + 1 if len(hit) else H

    order = np.argsort(-total)

    # Collapse overlapping matches: neighbours of a good match are also good
    # matches, and counting them would manufacture a sample size.
    picked: list[int] = []
    for j in order:
        if total[j] < 0:
            break
        if all(abs(cands[j]["end"] - cands[k]["end"]) >= w for k in picked):
            picked.append(int(j))
        if len(picked) >= args.stat_n:
            break

    out = dict(df=df, P=P, idx=idx, cur=cur, cands=cands, total=total,
               struct_sim=struct_sim, shape_sim=shape_sim,
               net=net, mfe=mfe, mae=mae, picked=picked, w=w, H=H,
               bars_to_expansion=bars_to_expansion, symbol=args.symbol,
               out_dir=args.out, top=args.top, session_hours=args.session_hours,
               levels=args.levels)
    report(out)


def binom_p(k: int, n: int, p0: float) -> float:
    """Two-sided exact binomial p-value for k successes in n at base rate p0.

    Replaces the fixed "10 percentage points = meaningful" rule this script
    first used, which called 3-of-5 a finding. With n in the dozens a 15-point
    gap can still be pure noise, and only the p-value says which.
    """
    from math import comb
    if n == 0:
        return 1.0
    pmf = [comb(n, i) * p0 ** i * (1 - p0) ** (n - i) for i in range(n + 1)]
    obs = pmf[k]
    return float(min(1.0, sum(v for v in pmf if v <= obs * (1 + 1e-9))))


def _label(net_v: float, mfe_v: float, mae_v: float, prior_trend: float) -> tuple[str, str]:
    """(direction, behaviour) for one forward window, in ATR units."""
    if net_v > 0.75:
        direction = "BULLISH"
    elif net_v < -0.75:
        direction = "BEARISH"
    else:
        direction = "FLAT"

    if direction == "FLAT":
        behaviour = "konsolidasiya"
        if mfe_v > 1.0 and mae_v < -1.0:
            behaviour = "iki terefli sweep"
    elif (net_v > 0) == (prior_trend > 0):
        behaviour = "davam (continuation)"
    else:
        behaviour = "donus (reversal)"
    return direction, behaviour


def report(S: dict) -> None:
    cur, cands, P, idx = S["cur"], S["cands"], S["P"], S["idx"]
    w, H = S["w"], S["H"]
    net, mfe, mae, total = S["net"], S["mfe"], S["mae"], S["total"]

    bar = "=" * 100
    print(bar)
    print(f"CARI 5M STRUKTUR -- {S['symbol']}   {idx[-1]:%Y-%m-%d %H:%M} NY   "
          f"(son {w} bar = {w*5/60:.1f} saat)")
    print(bar)
    seq = []
    for k in ("HH", "HL", "LH", "LL"):
        if cur[k.lower()]:
            seq.append(f"{k}x{cur[k.lower()]}")
    print(f"  Swing ardicilligi   : {', '.join(seq) if seq else 'tesdiqlenmis swing yoxdur'}"
          f"   (trend skoru {cur['trend']:+.2f}, {cur['n_swings']} swing)")
    print(f"  BOS                 : yuxari {cur['bos_up']}, asagi {cur['bos_dn']}   "
          f"son BOS istiqameti: {'YUXARI' if cur['last_bos_dir']>0 else 'ASAGI' if cur['last_bos_dir']<0 else 'yoxdur'}")
    print(f"  CHoCH / MSS         : {'YUXARI donus' if cur['choch']>0 else 'ASAGI donus' if cur['choch']<0 else 'yoxdur'}")
    print(f"  Likvidlik sweep     : yuxari {cur['sweep_hi']}, asagi {cur['sweep_lo']}")
    print(f"  Beraber high/low    : {cur['eq_hi']} / {cur['eq_lo']}")
    print(f"  Displacement        : yuxari {cur['disp_up']}, asagi {cur['disp_dn']}   "
          f"sonuncudan {cur['bars_since_disp']} bar kecib")
    print(f"  Doldurulmamis FVG   : yuxari {cur['fvg_up']}, asagi {cur['fvg_dn']}")
    prem = "PREMIUM" if cur["pos_in_range"] > 0.5 else "DISCOUNT"
    print(f"  Diapazonda movqe    : {cur['pos_in_range']*100:.0f}% ({prem})   "
          f"diapazon {cur['range_atr']:.1f} ATR")
    print(f"  Sixilma (son yari)  : {cur['compression']:.2f} ATR   pencere neti {cur['net_atr']:+.2f} ATR")
    print(f"  Volatillik / hecm   : ATR {cur['atr']:.3f} (oz tarixcesinin {cur['atr_pctl']*100:.0f}% faizli), "
          f"hecm normanin {cur['vol_regime']:.2f}x-i")

    # ---------------- base rate ----------------
    prior = np.array([d["net_atr"] for d in cands])
    n_all = len(net)
    bull_all = int((net > 0.75).sum())
    bear_all = int((net < -0.75).sum())
    flat_all = n_all - bull_all - bear_all

    print()
    print(bar)
    print(f"TARIXI UYGUNLUQ -- {len(cands)} pencere skorlandi, ust-uste dusenler birlesdirildi")
    print(bar)
    print(f"{'#':>2} {'TARIX (NY)':17s} {'UMUMI':>6} {'STRUK':>6} {'FORMA':>6} "
          f"{'ISTIQAMET':10s} {'DAVRANIS':20s} {'net':>7} {'MFE':>6} {'MAE':>6} {'bar':>4}")
    print("-" * 100)

    rows = []
    for rank, j in enumerate(S["picked"][:S["top"]], 1):
        d = cands[j]
        e = d["end"]
        direction, behaviour = _label(net[j], mfe[j], mae[j], d["net_atr"])
        b2e = S["bars_to_expansion"](e, d["atr"])
        rows.append(dict(rank=rank, j=j, end=e, ts=idx[e], direction=direction,
                         behaviour=behaviour, net=net[j], mfe=mfe[j], mae=mae[j], b2e=b2e))
        print(f"{rank:>2} {idx[e]:%Y-%m-%d %H:%M}  {total[j]:>6.1f} {S['struct_sim'][j]:>6.1f} "
              f"{S['shape_sim'][j]:>6.1f} {direction:10s} {behaviour:20s} "
              f"{net[j]:>+6.2f}A {mfe[j]:>+5.2f}A {mae[j]:>+5.2f}A {b2e:>4}")

    # Statistics use the FULL picked set, not the five shown above.
    pk = np.array(S["picked"], int)
    mn = net[pk]
    mfe_m = mfe[pk]
    mae_m = mae[pk]
    b2e_all = [S["bars_to_expansion"](cands[j]["end"], cands[j]["atr"]) for j in pk]
    nb = int((mn > 0.75).sum())
    nbe = int((mn < -0.75).sum())
    nf = len(mn) - nb - nbe

    print()
    print(bar)
    print(f"STATISTIKA -- {H} bar ({H*5/60:.1f} saat) irelide, ATR vahidi ile")
    print(bar)
    print(f"{'':32s} {'UYGUNLASAN':>14s} {'BAZA NISBETI':>14s} {'FERQ':>10s}")
    print("-" * 100)

    def line(name, k, base_k):
        n_m = len(mn)
        p0 = base_k / n_all
        pv = binom_p(k, n_m, p0)
        verdict = ("MENALI" if pv < 0.05 else "sesden ferqlenmir")
        print(f"{name:32s} {100*k/n_m:>13.1f}% {100*p0:>13.1f}% "
              f"{100*k/n_m - 100*p0:>+9.1f}   p={pv:.3f}  {verdict}")

    line("Bullish pay", nb, bull_all)
    line("Bearish pay", nbe, bear_all)
    line("Flat / ugursuz pay", nf, flat_all)
    print()
    print(f"{'Orta net hereket':32s} {mn.mean():>13.2f}A {net.mean():>13.2f}A {mn.mean()-net.mean():>+9.2f}")
    print(f"{'Orta MFE (maks lehine)':32s} {mfe_m.mean():>13.2f}A {mfe.mean():>13.2f}A {mfe_m.mean()-mfe.mean():>+9.2f}")
    print(f"{'Orta MAE (maks eleyhine)':32s} {mae_m.mean():>13.2f}A {mae.mean():>13.2f}A {mae_m.mean()-mae.mean():>+9.2f}")
    # Direction is only half the question. If matched windows expand harder or
    # sooner than average in BOTH directions, that is a volatility statement --
    # tradeable by a straddle-shaped setup, useless to a directional one -- and
    # it needs its own base rate or it cannot be read either.
    rng_m = np.abs(mn)
    rng_b = np.abs(net)
    print(f"{'Orta |hereket| (istiqametsiz)':32s} {rng_m.mean():>13.2f}A {rng_b.mean():>13.2f}A "
          f"{rng_m.mean()-rng_b.mean():>+9.2f}")
    ex_b = np.array([S["bars_to_expansion"](d["end"], d["atr"]) for d in cands[::37]])
    print(f"{'Genislenmeye qeder bar':32s} {np.mean(b2e_all):>14.1f} {np.mean(ex_b):>14.1f} "
          f"{np.mean(b2e_all)-np.mean(ex_b):>+9.1f}")
    print(f"{'  (baza her 37-ci pencereden, n=' + str(len(ex_b)) + ')':32s}")

    # Empirical p for the volatility claim: how often does a RANDOM set of the
    # same size reach this mean |move|? Eyeballing "+65%" is not evidence when
    # the matched set was chosen partly on volatility-adjacent features.
    rng_gen = np.random.default_rng(0)
    draws = rng_gen.choice(rng_b, size=(20000, len(rng_m)), replace=True).mean(axis=1)
    pv_vol = float((draws >= rng_m.mean()).mean())
    verdict_vol = "MENALI -- volatillik effekti realdir" if pv_vol < 0.05 else "sesden ferqlenmir"
    print()
    print(f"  |hereket| ucun empirik p = {pv_vol:.4f} ({verdict_vol}), "
          f"20000 tesadufi {len(rng_m)}-liq numune ile")
    print()
    print(f"Numune: {len(mn)} uygunlasan pencere (ust-uste dusenler birlesdirilib), "
          f"baza {n_all} pencere.")
    print("Yalniz FERQ sutunu mena dasiyir, ve onu da p-deyeri hell edir --")
    print("p >= 0.05 olan setir bu numune olcusunde sesden ferqlenmir.")

    if S.get("levels"):
        levels_report(S)

    if S["out_dir"]:
        charts(S, rows)


def _first_touch(P: dict, e: int, H: int, entry: float, sl: float, tp: float, long: bool) -> int:
    """+1 TP first, -1 SL first, 0 neither within H bars.

    Ties inside one bar resolve to the STOP. On 5M bars both levels are often
    inside the same candle and the tick order is unknowable from OHLC, so the
    pessimistic reading is the only defensible one -- the optimistic one is how
    backtests manufacture edges that evaporate live.
    """
    for k in range(e + 1, min(e + H + 1, P["n"])):
        hi, lo = P["h"][k], P["l"][k]
        if long:
            if lo <= sl:
                return -1
            if hi >= tp:
                return 1
        else:
            if hi >= sl:
                return -1
            if lo <= tp:
                return 1
    return 0


def levels_report(S: dict) -> None:
    P, idx, cands, H = S["P"], S["idx"], S["cands"], S["H"]
    pk = list(S["picked"])
    cur = S["cur"]
    price = float(P["c"][P["n"] - 1])
    a = cur["atr"]

    print()
    print("=" * 100)
    print(f"TP / SL SETIRLERI -- {S['symbol']} @ {price:.2f}, ATR(14) 5M = {a:.3f}")
    print("=" * 100)
    print("Her (SL, TP) cutu 50 uygunlasan pencerede bar-bar yoxlanilir: hansi evvel toxunur.")
    print("Beraberlik (ikisi de eyni barda) STOP sayilir -- 5M OHLC-de tick sirasi bilinmir.")
    print()

    for long in (True, False):
        side = "LONG" if long else "SHORT"
        print(f"--- {side} ---")
        print(f"{'SL(ATR)':>8} {'TP(ATR)':>8} {'R:R':>5} {'TP evvel':>9} {'SL evvel':>9} "
              f"{'ne biri':>8} {'gozlenen R':>11}")
        best = None
        for sl_a in (0.75, 1.0, 1.5, 2.0):
            for tp_a in (1.0, 1.5, 2.0, 3.0, 4.0):
                sl = price - sl_a * a if long else price + sl_a * a
                tp = price + tp_a * a if long else price - tp_a * a
                res = [_first_touch(P, cands[j]["end"], H,
                                    P["c"][cands[j]["end"]],
                                    P["c"][cands[j]["end"]] - sl_a * cands[j]["atr"] if long
                                    else P["c"][cands[j]["end"]] + sl_a * cands[j]["atr"],
                                    P["c"][cands[j]["end"]] + tp_a * cands[j]["atr"] if long
                                    else P["c"][cands[j]["end"]] - tp_a * cands[j]["atr"],
                                    long)
                       for j in pk]
                n = len(res)
                w_ = res.count(1); l_ = res.count(-1); o_ = res.count(0)
                rr = tp_a / sl_a
                expR = (w_ * rr - l_) / n
                if best is None or expR > best[0]:
                    best = (expR, sl_a, tp_a, w_, l_, o_, sl, tp)
                    res_best = res
                print(f"{sl_a:>8.2f} {tp_a:>8.2f} {rr:>5.2f} {100*w_/n:>8.0f}% {100*l_/n:>8.0f}% "
                      f"{100*o_/n:>7.0f}% {expR:>+11.2f}")
        expR, sl_a, tp_a, w_, l_, o_, sl, tp = best
        # Bootstrap the expectancy. A single +0.41R off 50 windows is one draw
        # from a wide distribution; if the interval straddles zero the number is
        # a description of these 50 windows, not a tradeable expectancy.
        outcomes = np.array([tp_a / sl_a if r == 1 else (-1.0 if r == -1 else 0.0)
                             for r in res_best])
        bs = np.random.default_rng(1).choice(outcomes, size=(10000, len(outcomes)),
                                             replace=True).mean(axis=1)
        lo_ci, hi_ci = np.percentile(bs, [5, 95])
        solid = lo_ci > 0
        verdict = ("MUSBET gozlenti" if expR > 0 else "MENFI gozlenti")
        print(f"  en yaxsi {side}: SL {sl_a:.2f}A / TP {tp_a:.2f}A  ->  "
              f"SL {sl:.2f}  TP {tp:.2f}")
        print(f"     gozlenen {expR:+.2f}R   90% CI [{lo_ci:+.2f}, {hi_ci:+.2f}]   {verdict}"
              f"{'  -- CI sifiri kesmir' if solid else '  -- CI SIFIRI KESIR, etibarli deyil'}")
        print()


def charts(S: dict, rows: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    P, idx, w, H = S["P"], S["idx"], S["w"], S["H"]
    outd = Path(S["out_dir"]); outd.mkdir(parents=True, exist_ok=True)

    def draw(ax, end, title, show_future):
        s = end - w + 1
        last = min(end + H, P["n"] - 1) if show_future else end
        xs = np.arange(s, last + 1)
        o, h, l, c = (P[k][s:last + 1] for k in ("o", "h", "l", "c"))
        up = c >= o
        ax.vlines(xs, l, h, color="#888", lw=0.6)
        ax.vlines(xs[up], o[up], c[up], color="#1a9850", lw=2.4)
        ax.vlines(xs[~up], o[~up], c[~up], color="#d73027", lw=2.4)
        if show_future:
            ax.axvline(end + 0.5, color="#0044cc", ls="--", lw=1.4)
            ax.text(end + 0.7, ax.get_ylim()[1], " struktur bitir", color="#0044cc",
                    fontsize=7, va="top")
        ph, pl = P["ph"][s:end + 1], P["pl"][s:end + 1]
        for i in range(len(ph)):
            if np.isfinite(ph[i]):
                ax.plot(s + i, ph[i], "v", color="#b2182b", ms=5)
            if np.isfinite(pl[i]):
                ax.plot(s + i, pl[i], "^", color="#2166ac", ms=5)
        d = P["displacement"][s:end + 1] > DISPLACEMENT_ATR
        for i in np.where(d)[0]:
            ax.axvspan(s + i - 0.5, s + i + 0.5, color="#ffd54f", alpha=0.35, zorder=0)
        ax.set_title(title, fontsize=8.5, loc="left")
        ax.tick_params(labelsize=6)
        ax.set_xticks([])
        ax.grid(alpha=0.15)

    n = len(rows) + 1
    fig, axes = plt.subplots(n, 1, figsize=(11, 2.6 * n))
    draw(axes[0], P["n"] - 1, f"INDI -- {S['symbol']} 5M, {idx[-1]:%Y-%m-%d %H:%M} NY", False)
    for ax, r in zip(axes[1:], rows):
        draw(ax, r["end"],
             f"#{r['rank']}  {r['ts']:%Y-%m-%d %H:%M} NY  --  {r['direction']} / {r['behaviour']}  "
             f"(net {r['net']:+.2f} ATR, MFE {r['mfe']:+.2f}, MAE {r['mae']:+.2f})", True)
    fig.suptitle(f"{S['symbol']} 5M -- cari struktur ve en yaxin tarixi uygunlar "
                 f"(qirmizi v = swing high, mavi ^ = swing low, sari = displacement)",
                 fontsize=9)
    fig.tight_layout(rect=[0, 0, 1, 0.98])
    p = outd / f"{S['symbol']}_structure_match.png"
    fig.savefig(p, dpi=125)
    print(f"\n[chart] {p}")


if __name__ == "__main__":
    main()
