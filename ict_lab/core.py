"""Setup detection (sweep -> MSS -> FVG/OB/breaker/fib entry) and bid/ask trade simulation.

Long and short share one code path: for s=-1 the bars are mirrored (H=-low, L=-high, O=-open,
C=-close), so every rule is written for a long after a sell-side sweep and converted back to raw
prices at the end.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ict_lab.data import Sym, pivots

KZ = {"london": (120, 300), "ny_am": (420, 600), "ny_pm": (810, 960)}
HTF = {"pdh", "pdl", "pwh", "pwl", "d20h", "d20l", "asiah", "asial", "lonh", "lonl"}
FLAT = 16 * 60                       # everything is closed at 16:00 NY
MIN_TARGET = {"XAUUSD": 1.5, "US100": 10.0, "EURUSD": 0.0010, "GBPUSD": 0.0010,
              "USDJPY": 0.10, "USDCAD": 0.0010, "AUDUSD": 0.0010, "US500": 3.0, "US30": 20.0,
              "USDCHF": 0.0010, "NDX100": 10.0}
MIN_RISK_SPREADS = 5.0               # a stop closer than 5 spreads is not a tradeable setup


@dataclass
class Cfg:
    key: str
    windows: list                    # [(label, start_min, end_min)] a sweep must START inside
    levels: tuple                    # kinds of liquidity that count as the swept level
    entry: str                       # fvg_ce | fib | ob | breaker | unicorn | close_back
    sl: str = "sweep"                # sweep | fvg_c1 | zone_far
    tp: str = "nearest"              # nearest | draw | asia_opp | pd_opp | po3 | ext | range_mid | ny
    mss_grace: int = 0               # minutes after the window end the MSS/FVG may still complete
    fill_after: int = 180            # minutes after the window end a resting order stays live
    fib_r: float = 0.705
    fib_ext: float = 0.27
    mo_rule: bool = False            # long only below the midnight open, short only above
    sd_feature: bool = False         # CBDR standard-deviation alignment counts as the 4th confluence


@dataclass
class Setup:
    sym: str
    strat: str
    day: int
    win: str
    d: int
    kind: str
    mode: str                        # limit | market | fib
    sig_ts: int
    a0: int                          # first M1 index the order is live
    exp: int                         # M1 index the order is cancelled at
    end: int                         # M1 index everything is flat at
    E: float
    sl: float
    inval: float                     # a bar closing beyond this cancels the resting order
    cancel_tp: float | None
    tp: dict                         # variant -> price (None when no valid target)
    X: float = 0.0                   # sweep extreme (fib mode)
    Y0: float = 0.0                  # impulse extreme at signal time (fib mode)
    fib_r: float = 0.0
    fib_ext: float = 0.0
    feats: dict = field(default_factory=dict)
    score: int = 0
    grade: str = "B"


def grade_of(score: int) -> str:
    return "A+" if score >= 4 else "A" if score == 3 else "B+" if score == 2 else "B"


class Mirror:
    """Mirrored copies of the M5 bars: s=+1 raw (longs), s=-1 flipped (shorts)."""

    def __init__(self, sym: Sym):
        f = sym.m5
        self.s = {}
        for s in (1, -1):
            if s == 1:
                H, L, O, C = f.h, f.l, f.o, f.c
            else:
                H, L, O, C = -f.l, -f.h, -f.o, -f.c
            self.s[s] = dict(H=H.tolist(), L=L.tolist(), O=O.tolist(), C=C.tolist(),
                             pl3=pivots(L, 3, False), ph2=pivots(H, 2, True), Ha=H, La=L)


def _pick(cands, E, SL, s, min_rr=2.0, min_dist=0.0):
    """Nearest target beyond E (direction s) that pays min_rr and is at least min_dist away."""
    risk = abs(E - SL)
    best = None
    for name, p in cands:
        dist = (p - E) * s
        if dist >= max(min_dist, min_rr * risk) and (best is None or dist < best[0]):
            best = (dist, name, p)
    return None if best is None else best[2]


def _targets(sym: Sym, lv: dict, s: int, sig_idx: int, E: float, SL: float, cfg: Cfg, kind: str,
             win_a: int, sweep_ext: float):
    """Raw-price target(s) for the strategy: returns {variant: price|None}."""
    up = s == 1
    risk = abs(E - SL)
    md = MIN_TARGET[sym.name]

    def side(hk, lk):
        return lv.get(hk if up else lk)

    def pool(*pairs):
        out = []
        for name, (hk, lk) in pairs:
            p = side(hk, lk)
            if p is not None:
                out.append((name, p))
        return out

    base = [("pd", ("pdh", "pdl")), ("pw", ("pwh", "pwl")), ("d20", ("d20h", "d20l")), ("asia", ("asiah", "asial"))]
    if win_a >= 420:
        base.append(("lon", ("lonh", "lonl")))
    out = {}
    if cfg.tp in ("nearest", "draw"):
        cands = pool(*base)
        if cfg.tp == "nearest":
            f = sym.m5
            hi = f.h if up else f.l
            lo_i = max(0, sig_idx - 288)
            for p in np.flatnonzero(pivots(hi[lo_i:sig_idx], 3, up)) + lo_i:
                if p + 3 < sig_idx:
                    after = hi[p + 1:sig_idx]
                    if (len(after) == 0) or ((after.max() < hi[p]) if up else (after.min() > hi[p])):
                        cands.append(("swing", float(hi[p])))
        out["doc"] = _pick(cands, E, SL, s, 2.0, md)
    elif cfg.tp == "asia_opp":
        cands = pool(("asia", ("asiah", "asial")))
        t = _pick(cands, E, SL, s, 2.0, md)
        out["doc"] = t if t is not None else _pick(pool(("pd", ("pdh", "pdl"))), E, SL, s, 2.0, md)
    elif cfg.tp == "pd_opp":
        out["doc"] = _pick(pool(("pd", ("pdh", "pdl"))), E, SL, s, 2.0, md)
        h, l = lv["pdh"], lv["pdl"]
        out["tp1"] = _pick([("mid", (h + l) / 2)], E, SL, s, 2.0, md)
    elif cfg.tp == "po3":
        t = _pick(pool(("pd", ("pdh", "pdl"))), E, SL, s, 2.0, md)
        out["doc"] = t if t is not None else _pick(pool(("pw", ("pwh", "pwl"))), E, SL, s, 2.0, md)
    elif cfg.tp == "range_mid":
        rng = {"pdh": ("pdh", "pdl"), "pdl": ("pdh", "pdl"), "pwh": ("pwh", "pwl"), "pwl": ("pwh", "pwl"),
               "d20h": ("d20h", "d20l"), "d20l": ("d20h", "d20l")}[kind]
        h, l = lv.get(rng[0]), lv.get(rng[1])
        if h is not None and l is not None:
            out["doc"] = _pick([("mid", (h + l) / 2)], E, SL, s, 2.0, md)
            out["opp"] = _pick([("opp", h if up else l)], E, SL, s, 2.0, md)
    elif cfg.tp == "ny":
        cands = pool(("lon", ("lonh", "lonl")), ("pd", ("pdh", "pdl")))
        if "cbdrh" in lv:
            r = lv["cbdrh"] - lv["cbdrl"]
            for n in (1, 2, 3):
                cands.append((f"sd{n}", lv["cbdrh"] + n * r if up else lv["cbdrl"] - n * r))
        out["doc"] = _pick(cands, E, SL, s, 2.0, md)
    return out


def build_setups(sym: Sym, mir: Mirror, cfg: Cfg, day: int, lv: dict) -> list[Setup]:
    f = sym.m5
    m1 = sym.m1
    res: list[Setup] = []
    d0, d1 = sym.days[day]
    for label, wa, wb in cfg.windows:
        i0, i1 = sym.win(day, wa, wb)
        if i1 - i0 < 3:
            continue
        sig_limit = day * 1440 + wb + cfg.mss_grace
        for s in (1, -1):
            T = mir.s[s]
            H, L, O, C = T["H"], T["L"], T["O"], T["C"]
            events = []
            fixed = []
            for kind in cfg.levels:
                if kind == "swing":
                    continue
                name = {"pd": ("pdl", "pdh"), "pw": ("pwl", "pwh"), "d20": ("d20l", "d20h"),
                        "asia": ("asial", "asiah"), "london": ("lonl", "lonh")}[kind][0 if s == 1 else 1]
                if kind == "london" and wa < 420:
                    continue
                p = lv.get(name)
                if p is not None:
                    fixed.append((name, s * p))
            for name, tl in fixed:
                j = None
                for k in range(i0, i1):
                    if L[k] < tl:
                        j = k
                        break
                if j is not None and (j == d0 or min(L[d0:j], default=1e18) >= tl):
                    events.append((j, tl, name))
            if "swing" in cfg.levels:
                pl3 = T["pl3"]
                for j in range(i0, i1):
                    best = None
                    for p in range(j - 4, max(j - 40, 0), -1):
                        if pl3[p] and L[p] > L[j] and min(L[p + 1:j], default=1e18) >= L[p]:
                            best = p
                            break
                    if best is not None:
                        events.append((j, L[best], "swing"))
            events.sort(key=lambda e: e[0])
            used_until = -1
            for j, tl, kind in events:
                if j <= used_until:
                    continue
                st = _one(sym, mir, cfg, s, day, lv, label, wa, wb, i0, j, tl, kind, sig_limit)
                if st is not None:
                    used_until = j
                    res.append(st)
    res.sort(key=lambda x: x.sig_ts)
    return res


def _one(sym, mir, cfg, s, day, lv, label, wa, wb, i0, j, tl, kind, sig_limit):
    f = sym.m5
    T = mir.s[s]
    H, L, O, C = T["H"], T["L"], T["O"], T["C"]
    ph2 = T["ph2"]
    n5 = len(H)
    sp = float(f.sp[j])
    ab, ar = sym.avg_body, sym.avg_rng
    if np.isnan(ab[j]) or j + 2 >= n5:
        return None
    # ---- turtle soup: close back inside, enter at once
    if cfg.entry == "close_back":
        q = None
        for k in range(j, min(j + 4, n5)):
            if C[k] >= tl:
                q = k
                break
        if q is None:
            return None
        X = min(L[j:q + 1])
        sig_idx = q
        sig_ts = int(f.ts[q]) + 300
        a0 = int(np.searchsorted(sym.m1.ts, sig_ts))
        if a0 >= len(sym.m1.ts):
            return None
        raw_o = float(sym.m1.o[a0])
        E = raw_o + (sym.m1.sp[a0] if s == 1 else 0.0)
        SL = s * (X - sp)
        mode, inval, ctp = "market", SL, None
        mss, fvg_k, zone = q, None, None
        disp = False
        Xraw = s * X
    else:
        # ---- MSS: first M5 close beyond the last confirmed swing, body at least half the range
        ref = None
        for p in range(j - 3, max(j - 48, 0), -1):
            if ph2[p] and H[p] > C[j]:
                ref = (p, H[p])
                break
        if ref is None:
            p = max(j - 12, 0)
            rp = int(np.argmax(H[p:j + 1])) + p
            ref = (rp, H[rp])
        rp, rv = ref
        mss = None
        for m in range(j + 1, min(j + 19, n5 - 2)):
            if int(f.wall[m]) + 5 > sig_limit:
                break
            if C[m] > rv and (C[m] - O[m]) >= 0.5 * (H[m] - L[m]):
                mss = m
                break
        if mss is None:
            return None
        xb = j + int(np.argmin(L[j:mss + 1]))
        X = L[xb]
        disp = any((C[k] - O[k]) >= 1.5 * ab[k] and (C[k] - O[k]) >= 0.6 * (H[k] - L[k]) for k in range(j + 1, mss + 1)
                   if not np.isnan(ab[k]))
        mingap = 0.1 * ar[j]
        fvg_k = None
        for k in range(j + 1, mss + 2):
            if k - 1 >= j and L[k] - H[k - 2] >= mingap and k - 2 >= xb - 1:
                fvg_k = k
                break
        if fvg_k is None:
            return None
        sig_idx = max(fvg_k, mss)
        if int(f.wall[sig_idx]) + 5 > sig_limit:
            return None
        sig_ts = int(f.ts[sig_idx]) + 300
        a0 = int(np.searchsorted(sym.m1.ts, sig_ts))
        if a0 >= len(sym.m1.ts):
            return None
        zl, zh = H[fvg_k - 2], L[fvg_k]
        Xraw = s * X
        buf = sp
        mode, ctp = "limit", None
        Y0 = max(H[j:sig_idx + 1])
        SLt = X - buf
        if cfg.entry == "fvg_ce":
            Et = (zl + zh) / 2
            if cfg.sl == "fvg_c1":
                SLt = L[fvg_k - 2] - buf
            inval_t = zl
        elif cfg.entry == "fib":
            mode = "fib"
            Et = Y0 - cfg.fib_r * (Y0 - X)
            inval_t = Y0 - 0.79 * (Y0 - X)
        elif cfg.entry in ("ob", "breaker", "unicorn"):
            if cfg.entry == "ob":
                cand = [k for k in range(xb, mss) if C[k] < O[k]]
                if not cand:
                    return None
                zk = cand[-1]
            else:
                zk = None
                for k in range(rp, max(rp - 8, 0), -1):
                    if C[k] > O[k]:
                        zk = k
                        break
                if zk is None:
                    return None
            bl, bh = L[zk], H[zk]
            if cfg.entry == "unicorn":
                lo, hi = max(bl, zl), min(bh, zh)
                if lo > hi:
                    return None
                Et = (lo + hi) / 2
            else:
                Et = (bl + bh) / 2
            SLt = bl - buf
            inval_t = bl
        else:
            return None
        E, SL, inval = s * Et, s * SLt, s * inval_t
        if mode == "fib":
            E = s * (Y0 - cfg.fib_r * (Y0 - X))
        # the move has already paid before the order could fill: no trade
        ctp = None
        # price already beyond the stop at signal time
        if (E - SL) * s <= 0:
            return None
    # ---- time bookkeeping
    fu = min(wb + cfg.fill_after, FLAT)
    m1 = sym.m1
    exp = int(np.searchsorted(m1.wall, day * 1440 + fu))
    end = int(np.searchsorted(m1.wall, day * 1440 + FLAT))
    if a0 >= end:
        return None
    tp = _targets(sym, lv, s, sig_idx, E, SL, cfg, kind, wa, Xraw)
    if cfg.entry == "fib" and cfg.tp == "ext":
        tp = {"doc": None}
    # ---- confluences (same five for every model, so the grades mean the same thing)
    bias = lv["bias"]
    lo_i = max(0, sig_idx - 288)
    hi_r, lo_r = float(f.h[lo_i:sig_idx + 1].max()), float(f.l[lo_i:sig_idx + 1].min())
    mid = (hi_r + lo_r) / 2
    pd_ok = (E < mid) if s == 1 else (E > mid)
    smt = _smt(sym, mir, s, j, xb if cfg.entry != "close_back" else j)
    if cfg.sd_feature and not smt and "cbdrh" in lv:
        r = lv["cbdrh"] - lv["cbdrl"]
        if r > 0:
            ext = Xraw
            base = lv["cbdrl"] if s == 1 else lv["cbdrh"]
            n = (base - ext) / r if s == 1 else (ext - base) / r
            smt = any(abs(n - k) <= 0.25 for k in (1, 2, 3))
    feats = {"bias": int(bias == s), "disp": int(bool(disp)), "pd": int(pd_ok), "smt": int(bool(smt)),
             "htf": int(kind in HTF)}
    if cfg.mo_rule and "mo" in lv:
        if (s == 1 and E >= lv["mo"]) or (s == -1 and E <= lv["mo"]):
            return None
    score = sum(feats.values())
    st = Setup(sym.name, cfg.key, day, label, s, kind, mode, sig_ts, a0, exp, end, E, SL, inval, ctp, tp,
               feats=feats, score=score, grade=grade_of(score))
    if mode == "fib":
        st.X, st.Y0, st.fib_r, st.fib_ext = Xraw, s * Y0, cfg.fib_r, (cfg.fib_ext if cfg.tp == 'ext' else 0.0)
    return st


def _smt(sym: Sym, mir: Mirror, s: int, j: int, xb: int) -> bool:
    """Did our sweep make a new extreme that the correlated instrument did not?"""
    T = mir.s[s]
    ph = sym.pair_h
    pl = sym.pair_l
    PL = pl if s == 1 else -ph
    if xb < 50 or xb + 2 >= len(PL):
        return False
    prior = PL[xb - 48:xb - 1]
    now = PL[xb - 1:xb + 2]
    if np.isnan(prior).all() or np.isnan(now).all():
        return False
    return bool(np.nanmin(now) > np.nanmin(prior))


def simulate(st: Setup, sym: Sym, variant: str):
    """Fill the setup on M1 bid/ask and trade it out; (R, fill_idx, exit_idx, why) or None if never filled."""
    m1 = sym.m1
    d = st.d
    a, e, x = st.a0, min(st.exp, st.end), st.end
    o = m1.o[a:x].tolist()
    h = m1.h[a:x].tolist()
    l = m1.l[a:x].tolist()
    c = m1.c[a:x].tolist()
    sp = m1.sp[a:x].tolist()
    n = len(o)
    ne = max(0, e - a)
    fill = None
    fi = -1
    SL = st.sl
    tp_doc = st.tp.get(variant) if variant != "r2" else None
    if st.mode == "market":
        if n == 0:
            return None
        fill = st.E
        fi = 0
    elif st.mode == "limit":
        for i in range(ne):
            if d == 1:
                if l[i] + sp[i] <= st.E:
                    fill, fi = min(st.E, o[i] + sp[i]), i
                    break
                if (c[i] < st.inval) or (st.cancel_tp is not None and h[i] >= st.cancel_tp):
                    return None
            else:
                if h[i] >= st.E:
                    fill, fi = max(st.E, o[i]), i
                    break
                if (c[i] + sp[i] > st.inval) or (st.cancel_tp is not None and l[i] + sp[i] <= st.cancel_tp):
                    return None
    else:  # fib: the limit follows the impulse extreme
        X, Y = st.X, st.Y0
        r = st.fib_r
        for i in range(ne):
            lvl = Y - d * r * abs(Y - X)
            if d == 1:
                if l[i] + sp[i] <= lvl:
                    fill, fi = min(lvl, o[i] + sp[i]), i
                    break
                if c[i] < Y - 0.79 * (Y - X) or l[i] <= SL:
                    return None
                Y = max(Y, h[i])
            else:
                if h[i] >= lvl:
                    fill, fi = max(lvl, o[i]), i
                    break
                if c[i] + sp[i] > Y + 0.79 * (X - Y) or h[i] + sp[i] >= SL:
                    return None
                Y = min(Y, l[i])
        if fill is not None and variant == "doc" and st.fib_ext:
            tp_doc = Y + d * st.fib_ext * abs(Y - X)
    if fill is None:
        return None
    risk = abs(fill - SL)
    if risk < MIN_RISK_SPREADS * sp[fi] or (fill - SL) * d <= 0:
        return None
    if variant == "r2":
        tp = fill + d * 2 * risk
    else:
        tp = tp_doc
        if tp is None or abs(tp - fill) < 2 * risk or (tp - fill) * d <= 0:
            return "skip"
    for i in range(fi, n):
        if d == 1:
            if l[i] <= SL:
                return (-(fill - SL) / risk, fi, i, "sl")
            if h[i] >= tp:
                return ((tp - fill) / risk, fi, i, "tp")
        else:
            if h[i] + sp[i] >= SL:
                return (-(SL - fill) / risk, fi, i, "sl")
            if l[i] + sp[i] <= tp:
                return ((fill - tp) / risk, fi, i, "tp")
    last = n - 1
    px = c[last] if d == 1 else c[last] + sp[last]
    return ((px - fill) * d / risk, fi, last, "time")
