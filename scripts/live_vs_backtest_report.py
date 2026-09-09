"""Weekly check: is the live account doing what the backtest said it would?

Every other test in this repo asks what a strategy did in the past. This asks
the only question the backtests cannot answer -- whether the deployed bots are
tracking their own expectation once real spread, slippage and execution delay
are in the way.

The live configuration is read out of the run_live_orb_breakout_*.bat files
rather than hardcoded, so the baseline always describes what is actually
deployed. Change a .bat and the comparison follows it.

Judgement is deliberately loose: with the handful of trades a 1-2 month sample
provides, a live PF anywhere near the walk-forward's honest 1.1-1.3 band is
consistent with the model. Only a sustained, one-sided gap means something
broke -- and that is what this is meant to catch early.

Usage:
    python -m scripts.live_vs_backtest_report
    python -m scripts.live_vs_backtest_report --days 30
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

import MetaTrader5 as mt5  # noqa: N813

import scripts.nasdaq_orb_m1_breakout_backtest as orb_mod
from scripts.consistency_analysis import _cached_load_m1, agg, consistency
from scripts.two_strategy_symbol_sweep import DATA_DIR, recent_spread

orb_mod.load_m1 = _cached_load_m1

REPO = Path(__file__).parent.parent
BREAKOUT_TAG = "setup_nasdaq_orb_m1"     # STRATEGY_TAG in run_live_nasdaq_orb.py
SWEEP_TAG = "setup_xauusd_orb"           # STRATEGY_TAG in run_live_xauusd_orb.py


def live_configs() -> dict[str, dict]:
    """Reads the deployed Breakout configs straight from the .bat files."""
    out = {}
    for bat in sorted(REPO.glob("run_live_orb_breakout_*_demo.bat")):
        text = bat.read_text(encoding="utf-8", errors="replace")
        def flag(name: str, default: str | None = None) -> str | None:
            m = re.search(rf"--{name}\s+(\S+)", text)
            return m.group(1) if m else default
        sym = flag("symbol")
        if not sym:
            continue
        out[sym] = dict(tp_r=float(flag("tp-r", "3.0")),
                        or_minutes=int(flag("or-minutes", "15")),
                        risk_pct=float(flag("risk-per-trade-pct", "0.005")),
                        bat=bat.name)
    return out


def closed_live_trades(days: int) -> dict[str, list[dict]]:
    """Closed positions on the connected account, grouped by symbol.

    R is taken from the exit reason rather than reconstructed from prices: the
    broker's close comment names the level that was hit, and every one of these
    strategies exits only at its own stop or target. Trades opened before
    stop/target were added to trade_opened (2026-09-09) have no other record of
    their risk distance.
    """
    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        frm = datetime.now(UTC) - timedelta(days=days)
        deals = mt5.history_deals_get(frm, datetime.now(UTC)) or []
        entries, exits = {}, {}
        for d in deals:
            (entries if d.entry == 0 else exits)[d.position_id] = d

        out: dict[str, list[dict]] = defaultdict(list)
        for pid, ex in exits.items():
            en = entries.get(pid)
            if en is None or not en.comment.startswith((BREAKOUT_TAG, SWEEP_TAG)):
                continue
            reason = "TP" if "[tp" in ex.comment else ("SL" if "[sl" in ex.comment else "?")
            out[en.symbol].append(dict(
                pid=pid, day=datetime.fromtimestamp(en.time, UTC).date(),
                strategy="Breakout" if en.comment.startswith(BREAKOUT_TAG) else "Sweep",
                entry=en.price, exit=ex.price, profit=ex.profit, reason=reason,
            ))
        return dict(out)
    finally:
        mt5.shutdown()


def backtest_baseline(symbol: str, cfg: dict, days: int) -> dict:
    csv = DATA_DIR / f"{symbol}_M1.csv"
    if not csv.exists():
        return {}
    sp = recent_spread(csv)
    tr = orb_mod.run_backtest(str(csv), "full", sp, cfg["tp_r"], "long",
                              or_minutes=cfg["or_minutes"], scan_minutes=1)
    t = [(date.fromisoformat(str(x.day)[:10]), x.r_multiple) for x in tr]
    n, wr, pf, net = agg([v for _, v in t])
    since = date.today() - timedelta(days=365)
    n1, wr1, pf1, net1 = agg([v for d, v in t if d >= since])
    return dict(n=n, wr=wr, pf=pf, net=net, wr_1y=wr1, pf_1y=pf1,
                green=consistency(t)["green_pct"],
                per_month=len(t) / max((max(d for d, _ in t) - min(d for d, _ in t)).days / 30.4, 1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="canli tarixceden nece gun geriye baxilsin")
    args = ap.parse_args()

    cfgs = live_configs()
    live = closed_live_trades(args.days)

    print("=" * 104)
    print(f"CANLI vs BACKTEST -- son {args.days} gun            {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")
    print("=" * 104)

    if not cfgs:
        print("Deploy olunmus Breakout konfiqurasiyasi tapilmadi (run_live_orb_breakout_*_demo.bat)")
        return

    total_profit = 0.0
    total_n = 0
    for sym, cfg in cfgs.items():
        rows = [r for r in live.get(sym, []) if r["strategy"] == "Breakout"]
        base = backtest_baseline(sym, cfg, args.days)
        print(f"\n### {sym}   ({cfg['or_minutes']}m OR / M1 / {cfg['tp_r']:g}R, risk {cfg['risk_pct']*100:g}%)")
        if not base:
            print("   backtest datasi yoxdur")
            continue
        print(f"   BACKTEST gozlentisi : PF {base['pf']:.3f} (son 1 il {base['pf_1y']:.3f})  "
              f"WR {base['wr']:.1f}%  yasil ay {base['green']:.0f}%  ~{base['per_month']:.1f} trade/ay")
        if not rows:
            print("   CANLI               : bu dovrde bagli trade yoxdur")
            continue
        wins = [r for r in rows if r["profit"] > 0]
        pnl = sum(r["profit"] for r in rows)
        gp = sum(r["profit"] for r in wins)
        gl = abs(sum(r["profit"] for r in rows if r["profit"] <= 0))
        pf_live = (gp / gl) if gl > 0 else float("inf")
        total_profit += pnl
        total_n += len(rows)
        print(f"   CANLI               : PF {pf_live:.3f}  WR {len(wins)/len(rows)*100:.1f}%  "
              f"n={len(rows)}  P&L ${pnl:+,.2f}")
        for r in sorted(rows, key=lambda x: x["day"]):
            print(f"      {r['day']}  {r['reason']:2}  giris {r['entry']:>10.2f}  "
                  f"cixis {r['exit']:>10.2f}  ${r['profit']:>+8.2f}")

        # Loose consistency check -- see module docstring on why the band is wide.
        if len(rows) >= 5:
            if pf_live < 0.7 * base["pf_1y"]:
                print("   >>> DIQQET: canli PF gozlentinin xeyli altindadir, arasdirilmalidir")
            elif pf_live > 1.0:
                print("   >>> gozlenti ile uyusur")

    print("\n" + "-" * 104)
    print(f"CEMI: {total_n} bagli trade, P&L ${total_profit:+,.2f}")
    if total_n < 20:
        print(f"Numune hele kicikdir ({total_n} trade) -- walk-forward gozlentisi (PF 1.1-1.3) ile")
        print("muqayise ucun en azi 20-30 trade lazimdir. Bu hesabat heftelik isledilmelidir.")


if __name__ == "__main__":
    main()
