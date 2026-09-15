"""Does the First FVG window paper bot trade what its backtest says, fill for fill?

Run it on the machine that runs the bot: the paper state lives there, and no history CSVs are
needed. It fetches the last N days of M1 and M15 bars from MT5, runs
scripts/first_fvg_window_backtest.py over them, reads the paper broker's state file and matches
the trades by setup id.

The comparison starts on the setup date of the bot's first order (or --since), because every
setup from before the bot existed would otherwise read as a trade it missed. Until the bot has
ordered anything there is nothing to compare, and the report says how many setups the backtest
saw in the meantime.

Both sides price fills with execution/level_fill.py, so on the same bars a correct bot matches
exactly. What a mismatch usually means:
  missing_in_paper     the bot was not running, or MT5 had not delivered the bar when it polled
  missing_in_backtest  the bot ordered a setup the backtest skips (e.g. the previous trade closed
                       between candle 3's close and the next poll)
  differs              bars changed after the fact, or a bug -- look at the detail

Usage:
    python -m scripts.first_fvg_paper_parity --symbol NDX100 --days 30
    python -m scripts.first_fvg_paper_parity --symbol NDX100 --days 30 --since 2026-09-15
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from scripts.first_fvg_window_backtest import FvgTrade, run_backtest  # noqa: E402
from strategy.first_fvg_window import FirstFvgWindowConfig  # noqa: E402

NY = ZoneInfo("America/New_York")
REPO = Path(__file__).parent.parent
PRICE_TOLERANCE = 0.005
LIMIT_TYPES = ("BUY_LIMIT", "SELL_LIMIT")


@dataclass(frozen=True)
class PaperTrade:
    setup_id: str
    entry_time: datetime
    entry: float
    stop: float
    target: float
    exit_time: datetime | None
    exit_price: float | None


@dataclass(frozen=True)
class ParityRow:
    setup_id: str
    status: str  # match | differs | missing_in_paper | missing_in_backtest | open
    detail: str = ""


def paper_trades_from_state(path: Path) -> list[PaperTrade]:
    """Filled limit orders from a PaperBroker(level_fills=True) state file, with their closing legs."""
    orders = list(json.loads(path.read_text(encoding="utf-8")).get("orders", {}).values())
    closes = {o["request"]["comment"]: o for o in orders
              if o["status"] == "FILLED" and o["request"]["order_type"] in ("BUY_MARKET", "SELL_MARKET")
              and o["request"]["comment"]}
    trades = []
    for o in orders:
        request = o["request"]
        if o["status"] != "FILLED" or request["order_type"] not in LIMIT_TYPES:
            continue
        close = closes.get(request["comment"])
        trades.append(PaperTrade(
            setup_id=request["comment"], entry_time=datetime.fromisoformat(o["filled_at"]),
            entry=o["fill_price"], stop=request["stop_loss"], target=request["take_profit"],
            exit_time=datetime.fromisoformat(close["filled_at"]) if close else None,
            exit_price=close["fill_price"] if close else None,
        ))
    return sorted(trades, key=lambda t: t.entry_time)


def comparison_start(state: Path, *, first_covered: date, since: date | None) -> date | None:
    """The first NY date to compare, or None when the bot has not ordered anything yet.

    Defaults to the setup date of the bot's earliest limit order -- filled, working or expired --
    and never goes before `first_covered`, the first day the fetched bars cover in full.
    """
    if since is not None:
        return max(since, first_covered)
    if not state.exists():
        return None
    orders = json.loads(state.read_text(encoding="utf-8")).get("orders", {}).values()
    setup_days = [
        datetime.fromisoformat(o["request"].get("valid_from") or o["created_at"]).astimezone(NY).date()
        for o in orders if o["request"]["order_type"] in LIMIT_TYPES
    ]
    return max(min(setup_days), first_covered) if setup_days else None


def compare(backtest: list[FvgTrade], paper: list[PaperTrade]) -> list[ParityRow]:
    by_bt = {t.setup_id: t for t in backtest}
    by_paper = {t.setup_id: t for t in paper}
    rows = []
    for sid in sorted(set(by_bt) | set(by_paper)):
        b, p = by_bt.get(sid), by_paper.get(sid)
        if p is not None and p.exit_price is None:
            rows.append(ParityRow(sid, "open"))
        elif b is None:
            rows.append(ParityRow(sid, "missing_in_backtest"))
        elif p is None:
            rows.append(ParityRow(sid, "missing_in_paper"))
        else:
            diffs = [f"{name} {bv:.2f} vs {pv:.2f}"
                     for name, bv, pv in (("entry", b.entry, p.entry), ("stop", b.stop, p.stop),
                                          ("target", b.target, p.target), ("exit", b.exit_price, p.exit_price))
                     if abs(bv - pv) > PRICE_TOLERANCE]
            if b.entry_time != p.entry_time:
                diffs.append(f"entry at {b.entry_time:%m-%d %H:%M} vs {p.entry_time:%m-%d %H:%M} UTC")
            if b.exit_time != p.exit_time:
                diffs.append(f"exit at {b.exit_time:%m-%d %H:%M} vs {p.exit_time:%m-%d %H:%M} UTC")
            rows.append(ParityRow(sid, "differs" if diffs else "match", "; ".join(diffs)))
    return rows


def main() -> None:
    from mt5.connector import MT5Connector

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--symbol", default="NDX100")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--since", type=date.fromisoformat,
                    help="Compare from this NY date (YYYY-MM-DD); defaults to the bot's first order")
    ap.add_argument("--tp-r", type=float, default=3.0)
    ap.add_argument("--session-start", default="10:00")
    ap.add_argument("--c1-bars-before", type=int, default=1)
    ap.add_argument("--third-candle-before", default="11:00")
    ap.add_argument("--state", help="Paper broker state file (defaults to the bot's own)")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    cfg = FirstFvgWindowConfig(
        session_start=time.fromisoformat(args.session_start), c1_bars_before=args.c1_bars_before,
        third_candle_before=time.fromisoformat(args.third_candle_before), tp_r=args.tp_r,
    )
    tag = args.symbol.lower().replace(".", "_")
    state = Path(args.state) if args.state else REPO / "risk" / f"paper_broker_state_fvg_window_{tag}.json"

    connector = MT5Connector()
    if not connector.connect():
        sys.exit("Could not connect to MT5.")
    try:
        m1 = connector.fetch_recent_bars(args.symbol, "M1", args.days * 1440)
        m15 = connector.fetch_recent_bars(args.symbol, "M15", args.days * 96)
    finally:
        connector.disconnect()

    spreads = [b.spread for b in m1 if b.spread > 0]
    spread = sum(spreads) / len(spreads) if spreads else 0.0
    # Only days whose whole session is inside both fetches can be compared fairly.
    first_covered = max(m1[0].timestamp, m15[0].timestamp).astimezone(NY).date() + timedelta(days=1)
    all_backtest = run_backtest(m15, m1, cfg, args.symbol, spread)
    start = comparison_start(state, first_covered=first_covered, since=args.since)

    if start is None:
        recent = [t for t in all_backtest if t.day >= first_covered]
        print(f"{args.symbol} First FVG window paritet: bot hele hec bir order qoymayib, muqayise edilecek sey yoxdur.")
        print(f"state: {state}{'' if state.exists() else '  (hele yaranmayib)'}")
        print(f"Backtest {first_covered} tarixinden beri {len(recent)} setup-a order qoyardi"
              + (f", sonuncusu {recent[-1].day}." if recent else "."))
        print("Bot hansi gunden isleyirse, --since YYYY-MM-DD ile o gunden muqayise edin.")
        sys.exit(0)

    backtest = [t for t in all_backtest if t.day >= start]
    paper = [t for t in (paper_trades_from_state(state) if state.exists() else [])
             if t.entry_time.astimezone(NY).date() >= start]
    rows = compare(backtest, paper)

    print(f"{args.symbol} First FVG window paritet, {start} -> bu gun, spread {spread:.2f}")
    print(f"state: {state}{'' if state.exists() else '  (YOXDUR -- bot bu masinda islemeyib?)'}")
    counts = {s: sum(1 for r in rows if r.status == s)
              for s in ("match", "differs", "missing_in_paper", "missing_in_backtest", "open")}
    print("  ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"backtest net R bu pencerede: {sum(t.r_net for t in backtest):+.2f} ({len(backtest)} trade)")
    for r in rows:
        if r.status != "match":
            print(f"  {r.status:20s} {r.setup_id}  {r.detail}")
    sys.exit(0 if counts["differs"] == counts["missing_in_paper"] == counts["missing_in_backtest"] == 0 else 1)


if __name__ == "__main__":
    main()
