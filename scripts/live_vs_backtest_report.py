"""Weekly check: is the live account doing what the backtest said it would?

Every other test in this repo asks what a strategy did in the past. This asks
the only question the backtests cannot answer -- whether the deployed bots are
tracking their own expectation once real spread, slippage and execution delay
are in the way.

The live configuration is read out of the run_live_orb_*_demo.bat files (by
backtest/live_replay/configs.parse_bat) rather than hardcoded, and the baseline
is the live-twin replay of exactly that configuration on the broker THIS machine
is logged into -- the same account whose deals are being counted, resolved from
.env by config/brokers.py. Change a .bat and the comparison follows it. The First
FVG bot (run_live_fvg_*_demo.bat) is not an ORB configuration the replay can model,
so its baseline is its own backtest with swap (scripts/fvg_window_envelope.py).

Run it where the account is: the VPS reports its CFI bots, the workstation its
FundingPips ones. A bot the roster does not allow real orders on this account is
skipped by name, so the Demo bots rostered on CFI are not reported as live there.

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
import sys
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

import MetaTrader5 as mt5  # noqa: N813

import config.brokers as machine
import run_live_first_fvg_window as fvg_runner
from backtest.live_replay.brokers import deployed_broker, history_path
from backtest.live_replay.configs import BotConfig, parse_bat, task_name
from backtest.live_replay.engine import run as replay
from backtest.live_replay.market import load_bars, load_fx, trim_to_real_m1
from backtest.live_replay.reversal import REVERSE_SUFFIX
from backtest.live_replay.specs import load_specs
from execution.stop_and_reverse import is_reverse
from mt5.connector import initialize_terminal
from scripts import account_orphans, kill_rule
from scripts.consistency_analysis import agg, consistency
from strategy.xauusd_orb_liquidity_sweep import XauusdOrbLiquiditySweepConfig

REPO = Path(__file__).parent.parent
BREAKOUT_TAG = "setup_nasdaq_orb_m1"     # STRATEGY_TAG in run_live_nasdaq_orb.py
SWEEP_TAG = "setup_xauusd_orb"           # STRATEGY_TAG in run_live_xauusd_orb.py
FVG_TAG = "setup_fvg_window"             # STRATEGY_TAG in strategy/first_fvg_window.py
FVG_FAMILY = "FvgWindow"


def _strategy_label(comment: str) -> str | None:
    """Which bot opened a deal, from its comment -- None for anything these bots did not open.

    A --reverse-on-stop trade carries its bot's tag too, but it is a different trade (a 0.5R target,
    not the strategy's own), so it is labelled apart and never mixed into the strategy's PF.
    """
    for tag, family in ((BREAKOUT_TAG, "Breakout"), (SWEEP_TAG, "Sweep")):
        if comment.startswith(tag):
            return f"{family} reversal" if is_reverse(comment, tag) else family
    if comment.startswith(FVG_TAG):
        return FVG_FAMILY
    return None


def _roster(path: Path = REPO / "deploy" / "demo_roster.txt",
            broker: str | None = None) -> set[str] | None:
    """Demo bots the version-controlled roster says are live -- the same file
    install_tasks.ps1 enables tasks from, so a bot whose .bat exists but is not
    deployed is not reported as if it were trading.

    `broker` narrows it to the account this machine trades: real-order permission is per
    account (see deploy/demo_roster.txt), so the CFI bot is not "live" on FundingPips.

    This used to ask Task Scheduler on the machine running the report. That
    stopped meaning anything on 2026-09-10: the bots moved to the VPS, every bot
    task on this workstation was disabled on purpose, and the 2026-09-14 report
    skipped all nine while the account had closed five trades that week.

    Returns None when the file is missing, so the report shows every bot rather
    than none.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    rows = [parts for parts in (line.split("#", 1)[0].split() for line in lines) if parts]
    return {parts[0] for parts in rows if broker is None or parts[1:2] == [broker]}


_task_name = task_name  # moved to configs so scripts/account_orphans.py reads the roster the same way


def deployed_fvg_launchers() -> list[Path]:
    """Every First FVG Demo launcher. The ORB replay cannot model this strategy, so these are
    reported beside deployed_configs() rather than parsed into BotConfigs."""
    return sorted(REPO.glob("run_live_fvg_*_demo.bat"))


def fvg_baseline() -> dict:
    """What the First FVG backtest expects on this machine's broker, swap charged -- or {} without
    history. It is measured on CFI because that is where the bot is rostered (see
    scripts/fvg_window_envelope.py); any other broker has no validated expectation to compare to."""
    from scripts.fvg_window_envelope import backtest_trades

    broker = deployed_broker()
    if broker.name != "cfi":
        return {}
    frame = backtest_trades(broker_name=broker.name)
    if frame.empty:
        return {}
    t = list(zip(frame["day"], frame["r_swap"]))
    n, wr, pf, net = agg([v for _, v in t])
    since = date.today() - timedelta(days=365)
    n1, wr1, pf1, net1 = agg([v for d, v in t if d >= since])
    span_months = max((max(d for d, _ in t) - min(d for d, _ in t)).days / 30.4, 1)
    return dict(n=n, wr=wr, pf=pf, net=net, wr_1y=wr1, pf_1y=pf1,
                green=consistency(t)["green_pct"], per_month=len(t) / span_months)


def deployed_configs() -> list[BotConfig]:
    """Every Demo launcher, read by the same parser the live-twin replay uses.

    This used to glob run_live_orb_breakout_*_demo.bat and key the result by symbol. Both went
    wrong on 2026-09-20: the one bot left deployed is run_live_orb_breakoutwf_xauusd_demo.bat,
    which that glob never matched, and it shares its ticker with the stood-down 15m breakout
    launcher, so a dict keyed by symbol would have kept whichever sorted last. parse_bat also
    reads every flag -- --weekend-flat, --reverse-on-stop, the risk fraction -- so the
    baseline below describes the bot that is really deployed.
    """
    return [parse_bat(p) for p in sorted(REPO.glob("run_live_orb_*_demo.bat"))]


def _label(config: BotConfig) -> str:
    if config.family == "breakout":
        label = f"{config.or_minutes}m OR / M{config.scan_minutes} / {config.tp_r:g}R"
        return label + (" / hefte sonu bagli" if config.weekend_flat else "")
    defaults = XauusdOrbLiquiditySweepConfig()
    end = config.entry_window_end or f"{defaults.entry_window_end:%H:%M}"
    return f"{config.scan_minutes}m OR / {end} / {defaults.fixed_tp_r:g}R"


def closed_live_trades(days: int) -> dict[str, list[dict]]:
    """Closed positions on the connected account, grouped by symbol.

    R is taken from the exit reason rather than reconstructed from prices: the
    broker's close comment names the level that was hit, and every one of these
    strategies exits only at its own stop or target. Trades opened before
    stop/target were added to trade_opened (2026-09-09) have no other record of
    their risk distance.
    """
    if not initialize_terminal():  # this checkout's terminal: two brokers share the VPS
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        frm = datetime.now(UTC) - timedelta(days=days)
        # MT5 reads these bounds on the broker's wall clock, which on CFI runs +3h ahead of real
        # UTC (measured 2026-09-21: tick epoch - time.time() = +10800 s). An upper bound of real
        # "now" therefore ended the window three hours in the past and dropped every deal closed
        # in the last ~3h before the report ran. Nothing is dated in the future, so padding the
        # end costs nothing.
        deals = mt5.history_deals_get(frm, datetime.now(UTC) + timedelta(days=1)) or []
        entries, exits = {}, {}
        for d in deals:
            (entries if d.entry == 0 else exits)[d.position_id] = d

        out: dict[str, list[dict]] = defaultdict(list)
        for pid, ex in exits.items():
            en = entries.get(pid)
            label = None if en is None else _strategy_label(en.comment)
            if label is None:
                continue
            reason = "TP" if "[tp" in ex.comment else ("SL" if "[sl" in ex.comment else "?")
            out[en.symbol].append(dict(
                pid=pid, day=datetime.fromtimestamp(en.time, UTC).date(),
                strategy=label,
                entry=en.price, exit=ex.price, profit=ex.profit, reason=reason,
            ))
        return dict(out)
    finally:
        mt5.shutdown()


def replay_baseline(config: BotConfig) -> dict:
    """What the live-twin replay expects of this bot, on this account's broker's prices.

    This used to call the batch backtests, which know nothing of --weekend-flat -- and the one
    bot deployed since 2026-09-20 is defined by it. The replay (backtest/live_replay) models
    that rule along with the spread, swap and poll clock the bot really trades under, on the
    broker this machine is logged into. Reverse legs are left out, as on the live side.

    Empty when this machine has no history for the broker: the VPS keeps none on purpose.
    """
    broker = deployed_broker()
    spec = load_specs(broker.specs_file)[config.symbol]
    path = history_path(broker, spec)
    if not path.exists():
        return {}
    try:
        fx = load_fx(spec.profit_currency, broker.data_dir)
    except FileNotFoundError:
        return {}
    m1 = trim_to_real_m1(load_bars(path, config.symbol, 1))  # CFI pads pre-2017 gold with non-M1 rows
    t = [(tr.entry_time.date(), tr.r) for tr in replay(config, m1, spec, fx, ticks=None)
         if tr.exit_reason != "OPEN" and not tr.setup_id.endswith(REVERSE_SUFFIX)]
    if not t:
        return {}
    n, wr, pf, net = agg([v for _, v in t])
    since = date.today() - timedelta(days=365)
    n1, wr1, pf1, net1 = agg([v for d, v in t if d >= since])
    span_months = max((max(d for d, _ in t) - min(d for d, _ in t)).days / 30.4, 1)
    return dict(n=n, wr=wr, pf=pf, net=net, wr_1y=wr1, pf_1y=pf1,
                green=consistency(t)["green_pct"], per_month=len(t) / span_months)


def _report_bot(sym: str, family: str, label: str, risk_pct: float, rows: list[dict],
                base: dict) -> tuple[int, float]:
    """Prints one bot's section and returns (closed trades, P&L) for the totals.

    Live trades are printed whether or not a backtest baseline exists. The VPS
    has no data/history CSVs on purpose, and while a missing baseline also
    skipped the live trades, a run there on 2026-09-14 reported "0 trades" for
    an account that had closed seven. Only the verdict needs the baseline.
    """
    print(f"\n### {sym} / {family}   ({label}, risk {risk_pct*100:g}%)")
    if base:
        print(f"   BACKTEST gozlentisi : PF {base['pf']:.3f} (son 1 il {base['pf_1y']:.3f})  "
              f"WR {base['wr']:.1f}%  yasil ay {base['green']:.0f}%  ~{base['per_month']:.1f} trade/ay")
    else:
        print("   BACKTEST            : backtest datasi yoxdur (bu masinda data/history yoxdur) -- yalniz canli")
    if not rows:
        print("   CANLI               : bu dovrde bagli trade yoxdur")
        return 0, 0.0
    wins = [r for r in rows if r["profit"] > 0]
    pnl = sum(r["profit"] for r in rows)
    gp = sum(r["profit"] for r in wins)
    gl = abs(sum(r["profit"] for r in rows if r["profit"] <= 0))
    pf_live = (gp / gl) if gl > 0 else float("inf")
    print(f"   CANLI               : PF {pf_live:.3f}  WR {len(wins)/len(rows)*100:.1f}%  "
          f"n={len(rows)}  P&L ${pnl:+,.2f}")
    for r in sorted(rows, key=lambda x: x["day"]):
        print(f"      {r['day']}  {r['reason']:2}  giris {r['entry']:>10.2f}  "
              f"cixis {r['exit']:>10.2f}  ${r['profit']:>+8.2f}")

    # Loose consistency check -- see module docstring on why the band is wide.
    if base and len(rows) >= 5:
        if pf_live < 0.7 * base["pf_1y"]:
            print("   >>> DIQQET: canli PF gozlentinin xeyli altindadir, arasdirilmalidir")
        elif pf_live > 1.0:
            print("   >>> gozlenti ile uyusur")
    return len(rows), pnl


def _report_reversals(rows: list[dict]) -> tuple[int, float]:
    """The bot's --reverse-on-stop trades, apart from its own (execution/stop_and_reverse.py)."""
    pnl = sum(r["profit"] for r in rows)
    gp = sum(r["profit"] for r in rows if r["profit"] > 0)
    gl = abs(sum(r["profit"] for r in rows if r["profit"] <= 0))
    pf = (gp / gl) if gl > 0 else float("inf")
    print(f"   REVERSAL (stopdan sonra eks trade): PF {pf:.3f}  n={len(rows)}  P&L ${pnl:+,.2f}")
    for r in sorted(rows, key=lambda x: x["day"]):
        print(f"      {r['day']}  {r['reason']:2}  giris {r['entry']:>10.2f}  "
              f"cixis {r['exit']:>10.2f}  ${r['profit']:>+8.2f}")
    return len(rows), pnl


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="canli tarixceden nece gun geriye baxilsin")
    args = ap.parse_args()

    live = closed_live_trades(args.days)
    deployed = deployed_configs()

    print("=" * 104)
    print(f"CANLI vs BACKTEST -- son {args.days} gun            {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")
    print("=" * 104)

    if not deployed:
        print("Deploy olunmus konfiqurasiya tapilmadi (run_live_orb_*_demo.bat)")
        return

    total_profit = 0.0
    total_n = 0
    profile = machine.local()
    roster = _roster(broker=profile.name)
    rules = kill_rule.load_rules()
    for config in sorted(deployed, key=lambda c: c.task):
        family = config.family.capitalize()   # "Breakout" | "Sweep", as _strategy_label says
        ticker = profile.ticker(config.symbol)  # the name THIS account's deals carry
        if roster is not None and config.task not in roster:
            print(f"\n### {config.task}   -- {profile.name} hesabinin rosterinde yoxdur, atlanir")
            continue
        rows = [r for r in live.get(ticker, []) if r["strategy"] == family]
        n, pnl = _report_bot(ticker, family, _label(config), config.risk_pct, rows,
                             replay_baseline(config))
        total_n += n
        total_profit += pnl
        # The pre-registered stop rule counts every trade since its adoption, not just --days' worth.
        if config.task in rules:
            prefix = BREAKOUT_TAG if family == "Breakout" else SWEEP_TAG
            rule = rules[config.task]
            for line in kill_rule.report_lines(rule, kill_rule.fetch_live_trades(rule, ticker, prefix)):
                print(line)
        reversals = [r for r in live.get(ticker, []) if r["strategy"] == f"{family} reversal"]
        if reversals:
            n, pnl = _report_reversals(reversals)
            total_n += n
            total_profit += pnl

    for bat in deployed_fvg_launchers():
        task = _task_name(bat.name)
        if roster is not None and task not in roster:
            print(f"\n### {task}   -- {profile.name} hesabinin rosterinde yoxdur, atlanir")
            continue
        args = fvg_runner.launcher_args(bat)
        ticker = profile.ticker(args.symbol)
        rows = [r for r in live.get(ticker, []) if r["strategy"] == FVG_FAMILY]
        label = f"{args.session_start} First FVG / M15 limit / {args.tp_r:g}R"
        n, pnl = _report_bot(ticker, FVG_FAMILY, label, args.risk_per_trade_pct, rows, fvg_baseline())
        total_n += n
        total_profit += pnl
        if task in rules:
            rule = rules[task]
            for line in kill_rule.report_lines(rule, kill_rule.fetch_live_trades(rule, ticker, FVG_TAG)):
                print(line)

    print("\n" + "-" * 104)
    print(f"CEMI: {total_n} bagli trade, P&L ${total_profit:+,.2f}")
    if total_n < 20:
        print(f"Numune hele kicikdir ({total_n} trade) -- walk-forward gozlentisi (PF 1.1-1.3) ile")
        print("muqayise ucun en azi 20-30 trade lazimdir. Bu hesabat heftelik isledilmelidir.")

    # What a stood-down bot left at the broker: nothing else looks (see scripts/account_orphans.py).
    print("\n" + "-" * 104)
    print("HESABDA HEC BIR BOTUN IDARE ETMEDIYI MOVQE / ORDER")
    try:
        orphans = account_orphans.fetch(profile)
    except RuntimeError as exc:  # includes WrongTerminalError / WrongBrokerError
        print(f"   YOXLANMADI: {exc}")
    else:
        for line in account_orphans.report_lines(orphans, profile):
            print(line)


if __name__ == "__main__":
    main()
