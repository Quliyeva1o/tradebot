"""Live ICT scanner + Telegram signals + paper tracking.

    python -m ict_lab.live              scan once, send alerts, update the paper book
    python -m ict_lab.live --dry-run    scan once, print what would be sent, change nothing
    python -m ict_lab.live --find-chat  print chat ids that wrote to the bot (send it /start first)
    python -m ict_lab.live --ping       send one test message

It is a SIGNAL bot: it reads M1 bars from the attached MT5 terminal (mt5.initialize(), no login, no
orders) and runs the same detectors the backtest used (ict_lab.core). Nothing is ever placed.

Each setup is tracked on paper under both exit rules the backtest used: `doc` (the document's target)
and `r2` (fixed 2R). One setup per strategy x symbol x window is live at a time: a later candidate only
opens once the earlier one died WITHOUT filling, which is how the backtest's "first candidate that
fills" reads when you cannot see the future.

Schedule it every 2 minutes (ict_lab/install_task.ps1). Telegram settings: ICT_TG_TOKEN / ICT_TG_CHAT_ID in .env.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from backtest.live_replay.market import server_wall_to_utc
from config.brokers import SERVER_CLOCKS
from ict_lab.core import FLAT, MIN_RISK_SPREADS, Mirror, Setup, build_setups
from ict_lab.data import Frame, Sym, to_m5
from ict_lab.strategies import CFGS

NY = ZoneInfo("America/New_York")
BAKU = ZoneInfo("Asia/Baku")
BARS = 45_000                  # ~32 trading days of M1: the levels need 21 sessions
ALERT_MAX_AGE_MIN = 45         # a setup older than this when first seen is tracked silently
FULL_ALERT_GRADES = ("A+",)    # these get one message each; A goes in one digest per scan
DIGEST_GRADES = ("A",)          # B+ is still tracked on paper, but never messaged

# Two terminals run on this machine and the bot reads either one (a process can only attach to one terminal, so each
# feed is its own process: --feed fp | cfi). Each feed keeps its own paper book; ict_lab.report lines them up.
FEEDS = {
    "fp": {"path": None, "clock": "fundingpips", "tickers": {}, "floors": {}, "state": "ict_lab/live_state.json"},
    "cfi": {"path": "C:/Program Files/MetaTrader 5/terminal64.exe", "clock": "cfi",
            "tickers": {"EURUSD": "EURUSD_", "GBPUSD": "GBPUSD_", "USDJPY": "USDJPY_", "USDCAD": "USDCAD_",
                        "AUDUSD": "AUDUSD_", "USDCHF": "USDCHF_", "XAUUSD": "XAUUSD_", "NDX100": "US100_Spot",
                        "SPX500": "US500_SPOT", "XAGUSD": "XAGUSD_"},
            "floors": {"XAUUSD": 0.15, "NDX100": 0.8}, "state": "ict_lab/live_state_cfi.json"},
}
FEED = "fp"


def ticker(name: str) -> str:
    return FEEDS[FEED]["tickers"].get(name, name)


def floor_of(name: str) -> float:
    return FEEDS[FEED]["floors"].get(name, SYMBOLS[name][0])


def state_path() -> Path:
    return Path(FEEDS[FEED]["state"])

# symbol -> (spread floor in price units, SMT partner). Floors are what the backtest charged at minimum.
SYMBOLS = {
    "EURUSD": (0.0001, "GBPUSD"),
    "GBPUSD": (0.00012, "EURUSD"),
    "USDJPY": (0.004, "USDCHF"),
    "USDCAD": (0.00006, "USDCHF"),
    "AUDUSD": (0.00004, "EURUSD"),
    "USDCHF": (0.00006, "USDJPY"),
    "XAUUSD": (0.19, "XAGUSD"),
    "NDX100": (2.0, "SPX500"),
}
PARTNERS = ("XAGUSD", "SPX500")
PIP = {"EURUSD": 0.0001, "GBPUSD": 0.0001, "AUDUSD": 0.0001, "USDCAD": 0.0001, "USDCHF": 0.0001,
       "USDJPY": 0.01, "XAUUSD": 0.1, "NDX100": 1.0}
NAMES = {
    "silver_bullet": "Silver Bullet", "london_asia_sweep": "London Asia Sweep", "power_of_3": "Power of 3",
    "ote": "OTE 0.705", "model_2022": "2022 Model", "unicorn": "Unicorn", "turtle_soup": "Turtle Soup",
    "order_block": "Order Block", "breaker_block": "Breaker Block", "ny_open_cbdr": "NY Open + CBDR",
    "pdh_pdl_raid": "PDH/PDL Raid",
}
VARIANTS = ("doc", "r2")


# --------------------------------------------------------------------------- data

def _frame(df: pd.DataFrame, point: float) -> Frame:
    naive = pd.to_datetime(df["time"], unit="s")
    utc = server_wall_to_utc(pd.Series(naive.values), SERVER_CLOCKS[FEEDS[FEED]["clock"]])
    ts = ((utc - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(seconds=1)).to_numpy(dtype=np.int64)
    order = np.argsort(ts, kind="stable")
    keep = np.r_[True, np.diff(ts[order]) != 0]
    sel = order[keep]
    g = lambda c: df[c].to_numpy(dtype=float)[sel]  # noqa: E731
    return Frame(ts[sel], g("open"), g("high"), g("low"), g("close"), df["spread"].to_numpy(dtype=float)[sel] * point)


OVERNIGHT_UNTIL = 120              # ny_pm setups are carried to 02:00 NY the next day
PAD_BARS = 2                   # see make_sym
ASOF: int | None = None      # --asof: pretend it is this UTC epoch (replays a past moment on the bars in the terminal)


def fetch(mt5, name: str) -> Frame | None:
    tk = ticker(name)
    mt5.symbol_select(tk, True)
    rates = None
    for _ in range(4):   # the terminal sometimes answers None while it syncs history
        rates = mt5.copy_rates_from_pos(tk, mt5.TIMEFRAME_M1, 1, BARS)   # pos 1: skip the forming minute
        if rates is not None and len(rates):
            break
        time.sleep(1.5)
    info = mt5.symbol_info(tk)
    if rates is None or len(rates) < 10_000 or info is None:
        return None
    f = _frame(pd.DataFrame(rates), info.point)
    if ASOF is not None:
        cut = int(np.searchsorted(f.ts, ASOF))
        f = Frame(f.ts[:cut], f.o[:cut], f.h[:cut], f.l[:cut], f.c[:cut], f.sp[:cut])
    # only whole M5 buckets: a half-built bar would look like a finished one to the detectors
    last = int(f.ts[-1])
    if last % 300 != 240:
        cut = int(np.searchsorted(f.ts, last - last % 300))
        f = Frame(f.ts[:cut], f.o[:cut], f.h[:cut], f.l[:cut], f.c[:cut], f.sp[:cut])
    return f


def make_sym(name: str, frames: dict[str, Frame]) -> Sym:
    floor, partner = floor_of(name), SYMBOLS[name][1]
    m1 = frames[name]
    m1.sp = np.maximum(m1.sp, floor)
    m5 = to_m5(m1, floor)
    real_m1 = m1
    if PAD_BARS:
        # build_setups wants one M1 bar AFTER the signal bar (the order goes live on it). Live that bar does not
        # exist yet, which would delay every alert by a minute; a flat placeholder lets the setup form at the
        # close. Only detection sees it -- status() reads the real minutes (sym.m1_real).
        lc = float(m1.c[-1])
        m1 = Frame(np.r_[m1.ts, m1.ts[-1] + 60], np.r_[m1.o, lc], np.r_[m1.h, lc], np.r_[m1.l, lc],
                   np.r_[m1.c, lc], np.r_[m1.sp, floor])
    if PAD_BARS:
        # core.py will not call an MSS until two more M5 bars exist behind it, which costs 10 minutes live.
        # Two flat placeholder bars at the last close satisfy that margin; they can never be the MSS bar
        # (the loop stops short of them) and a setup that leans on one has a signal time in the future,
        # which build_setups drops until the real bar arrives.
        k = np.arange(1, PAD_BARS + 1)
        last = float(m5.c[-1])
        flat = np.full(PAD_BARS, last)
        m5 = Frame(np.r_[m5.ts, m5.ts[-1] + 300 * k], np.r_[m5.o, flat], np.r_[m5.h, flat], np.r_[m5.l, flat],
                   np.r_[m5.c, flat], np.r_[m5.sp, np.full(PAD_BARS, floor)])
    pm5 = to_m5(frames[partner], 0.0)
    pos = np.minimum(np.searchsorted(pm5.ts, m5.ts), len(pm5.ts) - 1)
    ok = pm5.ts[pos] == m5.ts
    sym = Sym(name, floor, m1, m5, np.where(ok, pm5.h[pos], np.nan), np.where(ok, pm5.l[pos], np.nan))
    sym.m1_real = real_m1
    # today has fewer than 100 M5 bars for most of the session, so Sym leaves it out of tdays; levels() needs it in
    today = int(m5.day[-1])
    if (today + 3) % 7 < 5 and today not in sym.tindex:
        sym.tdays.append(today)
        sym.tindex[today] = len(sym.tdays) - 1
    return sym


# --------------------------------------------------------------------------- paper book

def status(st: Setup, sym: Sym, variant: str) -> dict:
    """Where this setup stands on the bars so far. Mirrors core.simulate bar for bar, but knows 'not yet'."""
    m1 = getattr(sym, "m1_real", sym.m1)
    n_all = len(m1.ts)
    # st.exp / st.end are M1 indices that sit at the END of the data until their clock time has passed,
    # so "has the deadline come" has to be read off the clock, not off the index
    cfg = CFGS[st.strat]
    wb = next(b for lab, _, b in cfg.windows if lab == st.win)
    last_wall = int(m1.wall[-1])
    # New York wall minute the order stops resting / the position is closed. The backtest flattens everything at
    # 16:00; a ny_pm setup (signal after 13:30) would get under half an hour, so the live book carries it to 02:00 NY next day.
    if st.win == "ny_pm":
        exp_to = hold_to = (st.day + 1) * 1440 + OVERNIGHT_UNTIL
    else:
        exp_to, hold_to = st.day * 1440 + min(wb + cfg.fill_after, FLAT), st.day * 1440 + FLAT
    expired = last_wall >= exp_to
    ended = last_wall >= hold_to
    end_i = int(np.searchsorted(m1.wall, hold_to))
    a, x = st.a0, min(end_i, n_all)
    e = min(int(np.searchsorted(m1.wall, exp_to)), end_i)
    ne = max(0, min(e, n_all) - a)
    o, h, l, c, sp = (v[a:x].tolist() for v in (m1.o, m1.h, m1.l, m1.c, m1.sp))
    if st.win == "ny_pm":
        # carrying past 17:00 NY crosses the broker rollover, where the quoted spread blows out for a few minutes.
        # That is not a cost anyone pays on a resting order, and the backtest never saw it (it flattened at 16:00).
        floor = floor_of(st.sym)
        sp = [floor if 1015 <= m <= 1025 else v for m, v in zip((m1.wall[a:x] % 1440).tolist(), sp)]
    n = len(o)
    d = st.d
    fill = fi = None
    if st.mode == "market":
        if n == 0:
            return {"state": "pending"}
        fill, fi = st.E, 0
    elif st.mode == "limit":
        for i in range(ne):
            if d == 1:
                if l[i] + sp[i] <= st.E:
                    fill, fi = min(st.E, o[i] + sp[i]), i
                    break
                if c[i] < st.inval or (st.cancel_tp is not None and h[i] >= st.cancel_tp):
                    return {"state": "dead", "why": "invalidated"}
            else:
                if h[i] >= st.E:
                    fill, fi = max(st.E, o[i]), i
                    break
                if c[i] + sp[i] > st.inval or (st.cancel_tp is not None and l[i] + sp[i] <= st.cancel_tp):
                    return {"state": "dead", "why": "invalidated"}
    else:  # fib: the limit follows the impulse extreme
        X, Y, r = st.X, st.Y0, st.fib_r
        for i in range(ne):
            lvl = Y - d * r * abs(Y - X)
            if d == 1:
                if l[i] + sp[i] <= lvl:
                    fill, fi = min(lvl, o[i] + sp[i]), i
                    break
                if c[i] < Y - 0.79 * (Y - X) or l[i] <= st.sl:
                    return {"state": "dead", "why": "invalidated"}
                Y = max(Y, h[i])
            else:
                if h[i] >= lvl:
                    fill, fi = max(lvl, o[i]), i
                    break
                if c[i] + sp[i] > Y + 0.79 * (X - Y) or h[i] + sp[i] >= st.sl:
                    return {"state": "dead", "why": "invalidated"}
                Y = min(Y, l[i])
        if fill is not None and variant == "doc" and st.fib_ext:
            tp_fib = Y + d * st.fib_ext * abs(Y - X)
    if fill is None:
        return {"state": "dead", "why": "expired"} if expired else {"state": "pending"}
    risk = abs(fill - st.sl)
    if (fill - st.sl) * d <= 0:   # no 5-spread minimum here: every fill is shown, tiny stops are flagged in the alert
        return {"state": "dead", "why": "beyond stop"}
    if variant == "r2":
        tp = fill + d * 2 * risk
    else:
        tp = tp_fib if (st.mode == "fib" and st.fib_ext and fill is not None) else st.tp.get("doc")
        if tp is None or abs(tp - fill) < 2 * risk or (tp - fill) * d <= 0:
            return {"state": "skip", "why": "no valid doc target"}
    base = {"fill": fill, "risk": risk, "tp": tp, "fill_ts": int(m1.ts[a + fi])}
    for i in range(fi, n):
        if d == 1:
            if l[i] <= st.sl:
                return {**base, "state": "closed", "R": -(fill - st.sl) / risk, "why": "sl"}
            if h[i] >= tp:
                return {**base, "state": "closed", "R": (tp - fill) / risk, "why": "tp"}
        else:
            if h[i] + sp[i] >= st.sl:
                return {**base, "state": "closed", "R": -(st.sl - fill) / risk, "why": "sl"}
            if l[i] + sp[i] <= tp:
                return {**base, "state": "closed", "R": (fill - tp) / risk, "why": "tp"}
    if ended:
        px = c[n - 1] if d == 1 else c[n - 1] + sp[n - 1]
        return {**base, "state": "closed", "R": (px - fill) * d / risk, "why": "time"}
    return {**base, "state": "open"}


def setup_id(st: Setup) -> str:
    return f"{st.sym}|{st.strat}|{st.day}|{st.win}|{st.d}|{st.kind}|{st.sig_ts}"


def group_key(st: Setup) -> str:
    return f"{st.sym}|{st.strat}|{st.day}|{st.win}"


# --------------------------------------------------------------------------- telegram

def _ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT   # Python 3.13's strict chain check rejects Telegram's GoDaddy chain; still fully verified
    return ctx


def tg_call(token: str, method: str, **params):
    data = urllib.parse.urlencode(params).encode() if params else None
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", data=data, timeout=20, context=_ctx()) as r:
        return json.load(r)


def send(token: str, chat: str, text: str) -> bool:
    """Sends to every chat id in `chat` (comma separated); True if at least one got it."""
    ok = False
    for cid in (c.strip() for c in chat.split(",")):
        if not cid:
            continue
        try:
            ok |= bool(tg_call(token, "sendMessage", chat_id=cid, text=text, disable_web_page_preview="true").get("ok"))
        except Exception as exc:   # one blocked chat must not stop the others or the paper book
            print(f"telegram send to {cid} failed: {type(exc).__name__}", file=sys.stderr)
    return ok


# --------------------------------------------------------------------------- messages

def fmt(sym: str, p: float) -> str:
    return f"{p:.2f}" if sym in ("XAUUSD", "NDX100") else f"{p:.3f}" if sym == "USDJPY" else f"{p:.5f}"


def _local(ts_ny_minutes: int) -> str:
    return datetime.fromtimestamp(ts_ny_minutes, timezone.utc).astimezone(BAKU).strftime("%H:%M")


def _doc_tp(st: Setup) -> tuple[float | None, bool]:
    """(target, approximate): OTE's target is an extension of the impulse as it stands when the order fills."""
    tp = st.tp.get("doc")
    if tp is None and st.mode == "fib" and st.fib_ext:
        return st.Y0 + st.d * st.fib_ext * abs(st.Y0 - st.X), True
    return tp, False


def tiny(st: Setup) -> bool:
    """Stop narrower than 5 spreads: the backtest refused these, a real fill would be at the mercy of slippage."""
    return abs(st.E - st.sl) < MIN_RISK_SPREADS * floor_of(st.sym)


def alert_text(group: list[Setup]) -> str:
    """One message for every model that fired on the same sweep (same symbol, side and window)."""
    st0 = group[0]
    s = st0.sym
    unit = "pt" if s == "NDX100" else "pip"
    ts = max(g.sig_ts for g in group)
    lines = [f"{'🟢' if st0.d == 1 else '🔴'} {st0.grade} · {s} {'LONG' if st0.d == 1 else 'SHORT'} · {st0.win} · sweep {st0.kind}"]
    for st in sorted(group, key=lambda g: g.strat):
        risk = abs(st.E - st.sl)
        tp, approx = _doc_tp(st)
        f = st.feats
        conf = "".join(k[0] if f.get(k) else "-" for k in ("bias", "disp", "pd", "smt", "htf"))
        tp_txt = (f"{'≈' if approx else ''}{fmt(s, tp)} ({abs(tp - st.E) / risk:.1f}R)" if tp else "yoxdur")
        kind = "bazar" if st.mode == "market" else "limit"
        lines.append(f"• {NAMES[st.strat]} [{conf}]" + ("  ⚠️ stop 5 spreaddən kiçik" if tiny(st) else ""))
        lines.append(f"  {kind} {fmt(s, st.E)} | SL {fmt(s, st.sl)} ({risk / PIP[s]:.1f} {unit})")
        lines.append(f"  TP sənəd {tp_txt} | TP 2R {fmt(s, st.E + st.d * 2 * risk)}")
    lines.append(f"Siqnal {datetime.fromtimestamp(ts, NY).strftime('%H:%M')} NY / {_local(ts)} Bakı · [b=bias d=disp p=pd s=smt h=htf]")
    if st0.win == "ny_pm":
        lines.append("⏳ ny_pm: order/mövqe ertəsi gün 02:00 NY (10:00 Bakı) vaxtınadək saxlanılır.")
    lines.append("Paper izlənir. Backtestdə bu qrupun edge-i sübut olunmayıb.")
    return "\n".join(lines)


def line_text(st: Setup) -> str:
    s = st.sym
    tp, approx = _doc_tp(st)
    return (f"{st.grade:>2} {s} {'L' if st.d == 1 else 'S'} {NAMES[st.strat]} | E {fmt(s, st.E)} SL {fmt(s, st.sl)}"
            + (f" TP {'≈' if approx else ''}{fmt(s, tp)}" if tp else " TP –") + (" ⚠️" if tiny(st) else ""))


def book_summary(state: dict, title: str, since_day: int | None = None) -> str:
    rows = [r for r in state["closed"] if since_day is None or r["day"] >= since_day]
    if not rows:
        return f"{title}\nBağlanmış paper trade yoxdur."
    df = pd.DataFrame(rows)
    out = [title]
    for g in ("A+", "A"):
        part = df[df.grade == g]
        if part.empty:
            continue
        doc = part.R_doc.dropna()
        r2 = part.R_r2.dropna()
        out.append(f"{g}: {len(part)} trade | doc {doc.sum():+.1f}R (orta {doc.mean():+.2f}) | 2R {r2.sum():+.1f}R (orta {r2.mean():+.2f})")
    return "\n".join(out)


# --------------------------------------------------------------------------- scan

@dataclass
class Cycle:
    token: str
    chat: str
    dry: bool
    state: dict
    new_full: list
    new_digest: list
    updates: list


def load_state() -> dict:
    if state_path().exists():
        return json.loads(state_path().read_text(encoding="utf8"))
    return {"setups": {}, "closed": [], "summary_day": 0}


def save_state(state: dict) -> None:
    tmp = state_path().with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf8")
    os.replace(tmp, state_path())


def scan(mt5, cyc: Cycle, symbols: list[str]) -> int:
    names = sorted(set(symbols) | {SYMBOLS[s][1] for s in symbols})
    frames = {}
    for nm in names:
        fr = fetch(mt5, nm)
        if fr is None:
            print(f"no data for {nm}", file=sys.stderr)
            continue
        frames[nm] = fr
    now = ASOF if ASOF is not None else int(datetime.now(timezone.utc).timestamp())
    seen = 0
    backfill = not cyc.state["setups"]   # a feed's first run also books the previous session, so two books can be compared
    twins: set[str] = set()
    for name in symbols:
        if name not in frames or SYMBOLS[name][1] not in frames:
            continue
        sym = make_sym(name, frames)
        mir = Mirror(sym)
        today = int(sym.m5.day[-1])
        days = [today]
        k = sym.tindex.get(today)
        if k and k >= 1:
            days.append(sym.tdays[k - 1])
        for day in days:
            lv = sym.levels(day)
            if lv is None or day not in sym.days:
                continue
            groups: dict[str, list[Setup]] = {}
            for key, cfg in CFGS.items():
                for st in build_setups(sym, mir, cfg, day, lv):
                    groups.setdefault(group_key(st), []).append(st)
            for cands in groups.values():
                cands.sort(key=lambda s: s.sig_ts)
                for st in cands:
                    sid = setup_id(st)
                    twin = f"{st.sym}|{st.strat}|{st.day}|{st.win}|{st.d}|{st.E:.6g}|{st.sl:.6g}"
                    if twin in twins and sid not in cyc.state["setups"]:
                        continue      # the same zone found again on a later bar: not a new setup
                    twins.add(twin)
                    rec = cyc.state["setups"].get(sid)
                    stats = {v: status(st, sym, v) for v in VARIANTS}
                    if rec is None:
                        if day != today and not backfill:
                            continue
                        age = (now - st.sig_ts) / 60
                        rec = {"id": sid, "sym": st.sym, "strat": st.strat, "grade": st.grade, "day": st.day, "win": st.win,
                               "d": st.d, "sig_ts": st.sig_ts, "alerted": age <= ALERT_MAX_AGE_MIN,
                               "states": {}, "scored": False}
                        if age <= ALERT_MAX_AGE_MIN and st.grade in FULL_ALERT_GRADES + DIGEST_GRADES:
                            (cyc.new_full if st.grade in FULL_ALERT_GRADES else cyc.new_digest).append(st)
                        if not cyc.dry:
                            cyc.state["setups"][sid] = rec
                    seen += 1
                    prev = rec["states"]
                    for v in VARIANTS:
                        cur = stats[v]
                        if prev.get(v) != cur["state"]:
                            notify = rec["alerted"] and rec["grade"] in FULL_ALERT_GRADES
                            if cur["state"] in ("open", "closed") and prev.get(v) not in ("open", "closed") and notify:
                                cyc.updates.append(f"{'🟢' if st.d == 1 else '🔴'} {st.grade} {st.sym} {NAMES[st.strat]} [{v}]: "
                                                   f"doldu @ {fmt(st.sym, cur['fill'])}")
                            if cur["state"] == "closed" and notify:
                                cyc.updates.append(f"{'✅' if cur['R'] > 0 else '❌'} {st.grade} {st.sym} {NAMES[st.strat]} [{v}]: "
                                                   f"{cur['why'].upper()} {cur['R']:+.2f}R")
                            prev[v] = cur["state"]
                    closed_all = all(stats[v]["state"] in ("closed", "dead", "skip") for v in VARIANTS)
                    if closed_all and not rec["scored"] and any(stats[v]["state"] == "closed" for v in VARIANTS):
                        cyc.state["closed"].append({"id": sid, "sym": st.sym, "strat": st.strat, "grade": st.grade, "day": st.day,
                                                    "R_doc": stats["doc"].get("R"), "R_r2": stats["r2"].get("R")})
                        rec["scored"] = True
                    if any(stats[v]["state"] in ("pending", "open") for v in VARIANTS):
                        break           # an earlier candidate is still live: later ones in the group wait
                    if any(stats[v]["state"] == "closed" for v in VARIANTS):
                        break           # the earlier candidate filled: that is this window's trade
    return seen


def run(args) -> int:
    from dotenv import dotenv_values
    env = dotenv_values(".env")
    token, chat = env.get("ICT_TG_TOKEN", ""), env.get("ICT_TG_CHAT_ID", "")
    if args.silent:
        token = chat = ""        # this feed only keeps its paper book; the other feed does the talking

    if args.find_chat:
        for u in tg_call(token, "getUpdates").get("result", []):
            m = u.get("message") or {}
            c = m.get("chat", {})
            print(c.get("id"), c.get("type"), c.get("first_name"), (m.get("text") or "")[:30])
        return 0
    if args.ping:
        print("sent" if send(token, chat, "ICT signal bot işləyir ✅") else "failed")
        return 0

    import MetaTrader5 as mt5
    path = FEEDS[FEED]["path"]
    if not (mt5.initialize(path=path) if path else mt5.initialize()):
        print("MT5 initialize failed", mt5.last_error(), file=sys.stderr)
        return 2
    try:
        if args.loop:
            return loop(mt5, args, token, chat)
        cycle(mt5, args, token, chat)
    finally:
        mt5.shutdown()
    return 0


def cycle(mt5, args, token: str, chat: str) -> int:
    symbols = args.symbols or list(SYMBOLS)
    state = load_state()
    cyc = Cycle(token, chat, args.dry_run, state, [], [], [])
    seen = scan(mt5, cyc, symbols)

    grouped: dict[tuple, list[Setup]] = {}
    for st in cyc.new_full:
        grouped.setdefault((st.sym, st.d, st.win, st.day), []).append(st)
    msgs = [alert_text(g) for g in grouped.values()]
    if cyc.new_digest:
        msgs.append(f"📋 {len(cyc.new_digest)} yeni A setup\n" + "\n".join(line_text(st) for st in cyc.new_digest))
    if cyc.updates:
        msgs.append("\n".join(cyc.updates))
    ny_now = datetime.now(NY)
    today_key = int(datetime(ny_now.year, ny_now.month, ny_now.day).timestamp() // 86400)
    if ny_now.hour >= 16 and ny_now.weekday() < 5 and state.get("summary_day") != today_key and not args.dry_run:
        msgs.append(book_summary(state, "📊 Paper nəticə · FundingPips"))
        other = FEEDS["cfi" if FEED == "fp" else "fp"]["state"]
        if Path(other).exists():
            msgs.append(book_summary(json.loads(Path(other).read_text(encoding="utf8")), "📊 Paper nəticə · CFI" if FEED == "fp" else "📊 Paper nəticə · FundingPips"))
        state["summary_day"] = today_key

    print(f"{datetime.now():%H:%M:%S} scanned {seen} setups | new full {len(cyc.new_full)} digest {len(cyc.new_digest)} updates {len(cyc.updates)}", flush=True)
    # state first: a crash while sending must not re-send the same alert on the next cycle
    if not args.dry_run:
        save_state(state)
    for m in msgs:
        if args.silent:
            continue
        if args.dry_run or not (token and chat):
            print("----\n" + m)
        else:
            send(token, chat, m)
    return seen


def _latest_bars(mt5) -> tuple:
    """Open time of the last completed M1 bar of every symbol: changes the moment a new minute closes."""
    out = []
    for nm in SYMBOLS:
        mt5.symbol_select(ticker(nm), True)
        r = mt5.copy_rates_from_pos(ticker(nm), mt5.TIMEFRAME_M1, 1, 1)
        out.append(int(r[0]["time"]) if r is not None and len(r) else 0)
    return tuple(out)


def loop(mt5, args, token: str, chat: str) -> int:
    """Stay resident and scan as soon as a new minute closes (about every 3 s it checks whether one did)."""
    print(f"{datetime.now():%H:%M:%S} loop started", flush=True)
    last = None
    last_scan = 0.0
    fails = 0
    while True:
        try:
            cur = _latest_bars(mt5)
            # symbols close their minute at slightly different moments; 12 s apart keeps the CPU quiet
            if cur != last and time.monotonic() - last_scan >= 12:
                seen = cycle(mt5, args, token, chat)
                last_scan = time.monotonic()
                if seen:                 # an empty scan means the terminal had no bars yet: try again, do not wait a minute
                    last = cur
            fails = 0
        except Exception as exc:   # a bad cycle must not kill the watcher; the next new minute retries it
            fails += 1
            print(f"{datetime.now():%H:%M:%S} cycle failed: {type(exc).__name__}: {str(exc)[:120]}", file=sys.stderr, flush=True)
            if fails >= 5:
                mt5.shutdown()
                time.sleep(5)
                if not mt5.initialize():
                    time.sleep(20)
                fails = 0
        time.sleep(3)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--find-chat", action="store_true")
    ap.add_argument("--ping", action="store_true")
    ap.add_argument("--loop", action="store_true", help="stay resident, scan on every new closed minute")
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--feed", choices=sorted(FEEDS), default="fp", help="which terminal to read: fp (default) or cfi")
    ap.add_argument("--silent", action="store_true", help="track the paper book only, send nothing to Telegram")
    ap.add_argument("--max-age", type=int, help="minutes a setup may be old and still alert (testing)")
    ap.add_argument("--asof", help='replay: pretend it is this New York time, "YYYY-MM-DD HH:MM" (implies --dry-run)')
    a = ap.parse_args()
    global FEED
    FEED = a.feed
    if a.max_age:
        global ALERT_MAX_AGE_MIN
        ALERT_MAX_AGE_MIN = a.max_age
    if a.asof:
        global ASOF
        ASOF = int(datetime.strptime(a.asof, "%Y-%m-%d %H:%M").replace(tzinfo=NY).timestamp())
        a.dry_run = True
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
