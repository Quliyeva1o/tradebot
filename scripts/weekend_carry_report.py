#!/usr/bin/env python
"""Which replayed trades were carried over a weekend, and what that carry cost.

The replay already models a weekend hold: engine/reversal walk M1 bars with no time bound, so a
trade open at Friday's last bar continues on Monday's first, and a stop hit on that bar is priced
as a post-break gap (SL_GAP_TICK from real ticks, else SL_GAP_PROXY, else SL_GAP_LEVEL). Swap is
charged at every server midnight crossed, including the Friday triple for the index CFDs. Nothing
here changes the engine -- it only labels finished trades, splitting the --reverse-on-stop legs
(setup_id suffix `_sar`) from the breakout entries that spawned them.
"""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import pandas as pd

from backtest.live_replay.configs import scope
from backtest.live_replay.engine import TradeRecord, run
from backtest.live_replay.market import BROKER_TZ, DEFAULT_DATA_DIR, load_fx, load_m1
from backtest.live_replay.reversal import REVERSE_SUFFIX
from backtest.live_replay.specs import load_specs
from backtest.live_replay.ticks import TickCache


def spans_weekend(trade: TradeRecord) -> bool:
    """True when a Saturday in broker time falls inside the hold."""
    a = pd.Timestamp(trade.entry_time).tz_convert(BROKER_TZ)
    b = pd.Timestamp(trade.exit_time).tz_convert(BROKER_TZ)
    day = a.normalize()
    while day <= b:
        if day.dayofweek == 5 and day > a:
            return True
        day += pd.Timedelta(days=1)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs", default="", help="comma-separated task names; default all")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--out", default="artifacts/live_replay/weekend_carry.csv")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    wanted = {n.strip() for n in args.configs.split(",") if n.strip()}
    configs = [c for c in scope() if not wanted or c.task in wanted]
    specs, ticks = load_specs(), TickCache(data_dir / "ticks")

    rows, summary = [], []
    for config in configs:
        spec = specs[config.symbol]
        m1 = load_m1(config.symbol, data_dir)
        fx = load_fx(spec.profit_currency, data_dir)
        trades = run(config, m1, spec, fx, ticks=ticks)
        carried = [t for t in trades if spans_weekend(t)]
        rev = [t for t in carried if t.setup_id.endswith(REVERSE_SUFFIX)]
        brk = [t for t in carried if not t.setup_id.endswith(REVERSE_SUFFIX)]
        all_rev = [t for t in trades if t.setup_id.endswith(REVERSE_SUFFIX)]
        first, last = pd.Timestamp(m1.ts[0], unit="s"), pd.Timestamp(m1.ts[-1], unit="s")
        span = f"{first.date()}..{last.date()}"
        summary.append({
            "task": config.task, "symbol": config.symbol,
            "reverse_on_stop": config.reverse_on_stop_r, "bars": span,
            "trades": len(trades), "reverse_trades": len(all_rev),
            "carried": len(carried), "carried_breakout": len(brk), "carried_reverse": len(rev),
            "carried_R": round(sum(t.r for t in carried), 2),
            "carried_reverse_R": round(sum(t.r for t in rev), 2),
            "all_R": round(sum(t.r for t in trades), 2),
        })
        for t in carried:
            rows.append({
                "task": config.task, "symbol": t.symbol,
                "leg": "reverse" if t.setup_id.endswith(REVERSE_SUFFIX) else "breakout",
                "direction": t.direction, "entry_time": t.entry_time, "exit_time": t.exit_time,
                "entry": t.entry, "stop": t.stop, "exit": t.exit, "exit_reason": t.exit_reason,
                "r": round(t.r, 3), "pnl_usd": round(t.pnl_usd, 2),
                "swap_usd": round(t.swap_usd, 2),
            })
        print(f"--- {config.task:<28} bars {span}  trades {len(trades):>4}  "
              f"reverse {len(all_rev):>3}  carried {len(carried):>3} "
              f"(breakout {len(brk)}, reverse {len(rev)})")

    sm = pd.DataFrame(summary)
    print("\n" + sm.to_string(index=False))
    print(f"\ntotals: {sm.carried.sum()} weekend-carried trades, "
          f"of which {sm.carried_reverse.sum()} are reverse legs")
    if rows:
        df = pd.DataFrame(rows)
        print("\nexit reasons of the carried trades:")
        for leg, g in df.groupby("leg"):
            print(f"  {leg:<9} {dict(Counter(g.exit_reason))}  net R {g.r.sum():+.2f}")
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)
        print(f"\nwrote {out}")
    else:
        print("\nno weekend-carried trade in this data window -- nothing to score")


if __name__ == "__main__":
    main()
