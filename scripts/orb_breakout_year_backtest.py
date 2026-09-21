"""One year of the ORB breakout bots, replayed on each broker's own prices and costs.

Why this exists beside scripts/live_replay_backtest.py: that one answers "what would the
deployed roster have done, on FundingPips, over its whole history". This answers the question
asked before opening an account somewhere else -- "what would the SAME bots have done over the
LAST 12 MONTHS on THIS broker's feed" -- so it runs every breakout launcher, Paper case and
Demo case alike, against each broker profile in turn and puts the two side by side.

What differs per broker, and is therefore read per broker rather than shared:
  - the history CSV (its own bid bars AND its own per-bar spread column),
  - the ticker (CFI calls NDX100 "US100_Spot" -- SymbolSpec.broker_symbol names the file),
  - contract size, point, margin rate, and the FX rate file for EUR/JPY-quoted indices,
  - swap: FundingPips charges annual percent (mode 5), CFI money per lot per day (mode 4),
  - commission per lot.

Everything else is held identical on purpose, so a difference in the table is a difference in
broker and not in method: same window, same $50,000 starting balance, same one-position gate,
both brokers replayed WITHOUT tick data (CFI has none, so FundingPips' ticks are not used
either -- post-break stops are priced by the same proxy on both sides).

Each bot is replayed standalone on its own $50,000, exactly as LIVE_REPLAY_BACKTEST_REPORT.md
does. The sum of the R column is NOT a portfolio result: six bots on one real account share a
balance and a margin ceiling, and drawdowns overlap.

Usage:
    python -m scripts.orb_breakout_year_backtest
    python -m scripts.orb_breakout_year_backtest --brokers cfi --days 365
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from statistics import median

sys.stdout.reconfigure(encoding="utf-8")
sys.path.append(str(Path(__file__).parent.parent.resolve()))

import numpy as np

from backtest.live_replay.brokers import BROKERS, Broker, history_path
from backtest.live_replay.configs import REPO, BotConfig, load_roster, parse_bat
from backtest.live_replay.engine import TradeRecord, run
from backtest.live_replay.market import BarFrame, FxSeries, load_bars, load_fx
from backtest.live_replay.metrics import equity_curve, stats
from backtest.live_replay.pricing import swap_usd
from backtest.live_replay.specs import SymbolSpec, load_specs
from core.models import SignalDirection

START_BALANCE = 50_000.0


def period(days: int) -> str:
    """The window in the words a reader uses: "1 il", "3 ay", or a plain day count."""
    if days % 365 == 0:
        return f"{days // 365} il"
    if days % 30 == 0:
        return f"{days // 30} ay"
    return f"{days} gün"


def report_path(days: int) -> Path:
    """One file per window, so a short run never overwrites a longer one's report."""
    tag = {365: "1Y", 180: "6MO", 90: "3MO"}.get(days, f"{days}D")
    return Path(f"ORB_BREAKOUT_{tag}_BROKER_REPORT.md")


@dataclass
class Result:
    broker: str
    config: BotConfig
    deployed: bool           # a Demo task the roster actually lets place orders
    trades: list[TradeRecord]
    median_spread: float     # price units, over this window's bars
    financing_pct: float     # a long's swap as annual percent of notional, today's rate
    capped_pct: float        # share of trades stuck at the broker's volume_max
    commission_per_lot: float
    volume_max: float

    @property
    def closed(self) -> list[TradeRecord]:
        return [t for t in self.trades if t.exit_reason != "OPEN"]


def breakout_configs(repo: Path = REPO) -> list[BotConfig]:
    """Every ORB breakout launcher on the six symbols, Demo first, then Paper.

    Unlike configs.scope() this keeps BOTH cases even where they are the same strategy, because
    the question is explicitly about the Paper case and the Demo case of each symbol. The
    inverse (`--inverse`) and weekend-flat (`--weekend-flat`) paper bots are kept too: they are
    live bots on these symbols.
    """
    configs = [parse_bat(p) for p in sorted(repo.glob("run_live_orb_*.bat"))]
    breakout = [c for c in configs if c.family == "breakout"]
    return sorted(breakout, key=lambda c: (c.symbol, c.paper, c.task))


def last_bar_day(path: Path) -> date:
    """The final row's broker-local date, read from the end of the file.

    These CSVs run to hundreds of megabytes and only the last line is wanted, so the tail is
    seeked to rather than parsed. Broker-local is close enough here: it only picks the window's
    last full day, and the replay is given a UTC bound afterwards.
    """
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - 4096))
        last = handle.read().splitlines()[-1].decode()
    return datetime.strptime(last.split(",")[0], "%Y-%m-%d %H:%M:%S").date()


def window(brokers: list[Broker], configs: list[BotConfig], days: int) -> tuple[datetime, datetime]:
    """The last `days` of history every broker actually has, for every symbol in scope.

    The end is the EARLIEST last bar across broker/symbol, so no bot gets a few extra days of
    a different market than another.
    """
    ends = [last_bar_day(history_path(broker, load_specs(broker.specs_file)[symbol]))
            for broker in brokers
            for symbol in {c.symbol for c in configs}]
    end = datetime.combine(min(ends), datetime.min.time(), tzinfo=UTC) + timedelta(days=1)
    return end - timedelta(days=days), end


def financing_pct(spec: SymbolSpec, m1: BarFrame, fx: FxSeries) -> float:
    """One long lot's overnight swap as an annual percent of what that lot is worth.

    The two brokers quote swap in different units (annual percent vs money per lot per night),
    and their contract sizes differ as well, so neither raw rate can be compared across them.
    Cost per unit of exposure can be, and it is what a trade actually pays.
    """
    price, usd = float(m1.close[-1]), fx.usd_per_unit(int(m1.ts[-1]))
    notional = spec.contract_size * price * usd
    nightly = swap_usd(spec, SignalDirection.BUY, 1.0, price, 1, usd)
    return 100.0 * nightly * 360.0 / notional


def replay(broker: Broker, configs: list[BotConfig], start: datetime, end: datetime,
           out_dir: Path) -> list[Result]:
    specs = load_specs(broker.specs_file)
    roster = load_roster()
    results: list[Result] = []
    bars: dict[str, BarFrame] = {}
    for config in configs:
        spec = specs[config.symbol]
        if config.symbol not in bars:  # one ~50MB read per symbol, reused by its two cases
            bars[config.symbol] = load_bars(history_path(broker, spec), config.symbol, 1, start, end)
        m1 = bars[config.symbol]
        fx = load_fx(spec.profit_currency, broker.data_dir)
        # ticks=None on purpose: see the module docstring.
        trades = run(config, m1, spec, fx, ticks=None, start_balance=START_BALANCE)
        capped = [t for t in trades if t.volume >= spec.volume_max - 1e-9]
        result = Result(broker=broker.name, config=config,
                        deployed=not config.paper and config.task in roster, trades=trades,
                        median_spread=float(np.median(m1.spread)),
                        financing_pct=financing_pct(spec, m1, fx),
                        capped_pct=100.0 * len(capped) / len(trades) if trades else 0.0,
                        commission_per_lot=spec.commission_per_lot_usd, volume_max=spec.volume_max)
        results.append(result)
        write_trades(out_dir / broker.name / f"{config.task}_trades.csv", trades)
        closed = result.closed
        print(f"  {config.task:34s} n={len(closed):4d} netR={stats([t.r for t in closed]).net_r:+8.1f}")
    return results


def write_trades(path: Path, trades: list[TradeRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(trades[0]).keys()) if trades
                                else ["setup_id"])
        writer.writeheader()
        for trade in trades:
            writer.writerow(asdict(trade))


def case(config: BotConfig) -> str:
    """The launcher's own words for what this bot is, in one column."""
    if config.inverse:
        return "Paper (inverse)"
    if config.weekend_flat:
        return "Paper (həftə sonu bağlı)"
    if config.paper:
        return "Paper"
    return "Demo" + (f" (rev {config.reverse_on_stop_r:g}R)" if config.reverse_on_stop_r else "")


def params(config: BotConfig) -> str:
    return f"{config.or_minutes}m OR / M{config.scan_minutes} / {config.tp_r:g}R"


def money(value: float) -> str:
    return f"{'-' if value < 0 else ''}${abs(value):,.0f}"


def summary_rows(results: list[Result]) -> list[dict]:
    rows = []
    for result in results:
        closed = result.closed
        st = stats([t.r for t in closed])
        balance, dd_pct = equity_curve([t.pnl_usd for t in closed], START_BALANCE)
        rows.append(dict(
            task=result.config.task, broker=result.broker, symbol=result.config.symbol,
            case=case(result.config), params=params(result.config), deployed=result.deployed,
            n=st.n, win_pct=st.win_pct, pf=st.pf, net_r=st.net_r, max_dd_r=st.max_dd_r,
            balance=balance, dd_pct=dd_pct, ret_pct=100.0 * (balance - START_BALANCE) / START_BALANCE,
            swap=sum(t.swap_usd for t in closed), commission=sum(t.commission_usd for t in closed),
            open_trades=len(result.trades) - len(closed),
            tp=sum(t.exit_reason == "TP" for t in closed),
            sl=sum(t.exit_reason.startswith("SL") for t in closed),
            median_spread=result.median_spread, financing_pct=result.financing_pct,
            capped_pct=result.capped_pct,
            risk_median=median([t.risk_usd for t in closed]) if closed else 0.0,
            commission_per_lot=result.commission_per_lot, volume_max=result.volume_max,
        ))
    return rows


def pf(value: float) -> str:
    return "inf" if value == float("inf") else f"{value:.3f}"


def render(rows: list[dict], brokers: list[Broker], start: datetime, end: datetime,
           generated: datetime) -> str:
    by_task: dict[str, dict[str, dict]] = {}
    for row in rows:
        by_task.setdefault(row["task"], {})[row["broker"]] = row
    order = [t for t in dict.fromkeys(r["task"] for r in rows)]
    names = [b.name for b in brokers]

    days = (end - start).days
    out = [
        f"# ORB Breakout — son {period(days)}, broker müqayisəsi",
        "",
        f"Hazırlandı: {generated:%Y-%m-%d %H:%M} UTC · pəncərə: "
        f"**{start:%Y-%m-%d} → {end - timedelta(days=1):%Y-%m-%d}** ({days} gün) · "
        f"hər bot ayrıca {money(START_BALANCE)} hesabda · skript: `scripts/orb_breakout_year_backtest.py`",
        "",
        "Brokerlər: " + " · ".join(f"**{b.label}** (`{b.data_dir}`)" for b in brokers),
        "",
        "## Əsas cədvəl",
        "",
        "| Bot | Simvol | Hal | Parametr | " + " | ".join(
            f"{b.label}: n / PF / netR / nəticə" for b in brokers) + (" | fərq |" if len(brokers) > 1 else " |"),
        "|---|---|---|---|" + "---|" * len(brokers) + ("---|" if len(brokers) > 1 else ""),
    ]
    for task in order:
        row = next(iter(by_task[task].values()))
        cells = []
        for name in names:
            r = by_task[task].get(name)
            cells.append("—" if r is None else
                         f"{r['n']} / {pf(r['pf'])} / {r['net_r']:+.1f}R / {money(r['balance'] - START_BALANCE)}")
        if len(brokers) > 1:
            first, last = by_task[task].get(names[0]), by_task[task].get(names[-1])
            cells.append("—" if first is None or last is None else
                         f"{last['net_r'] - first['net_r']:+.1f}R")
        flag = "" if row["deployed"] or row["case"].startswith("Paper") else " ⚠️"
        out.append(f"| {task}{flag} | {row['symbol']} | {row['case']} | {row['params']} | "
                   + " | ".join(cells) + " |")
    out += [
        "",
        "⚠️ = `deploy/demo_roster.txt`-də olmayan Demo konfiqurasiyası: .bat faylı var, amma "
        "hazırda real order vermir (o simvolu başqa strategiya tutur).",
        "",
        "## Hər broker ayrıca",
        "",
    ]
    for broker in brokers:
        out += [
            f"### {broker.label}",
            "",
            "| Bot | n | qazanc % | PF | netR | maxDD R | TP | SL | son balans | maxDD % | gəlir % | swap $ | komissiya $ | açıq |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for task in order:
            r = by_task[task].get(broker.name)
            if r is None:
                continue
            out.append(
                f"| {task} | {r['n']} | {r['win_pct']:.1f}% | {pf(r['pf'])} | {r['net_r']:+.1f} | "
                f"{r['max_dd_r']:.1f} | {r['tp']} | {r['sl']} | {money(r['balance'])} | "
                f"{r['dd_pct']:.1f}% | {r['ret_pct']:+.1f}% | {money(r['swap'])} | "
                f"{money(r['commission'])} | {r['open_trades']} |")
        out.append("")
    out += cost_section(rows, brokers) + notes_section(rows, brokers)
    return "\n".join(out) + "\n"


def cost_section(rows: list[dict], brokers: list[Broker]) -> list[str]:
    """What each broker charges for the same exposure -- the reason the netR columns differ."""
    by_symbol: dict[str, dict[str, dict]] = {}
    for row in rows:
        by_symbol.setdefault(row["symbol"], {}).setdefault(row["broker"], row)
    out = [
        "## Xərc müqayisəsi (eyni ekspozisiyaya görə)",
        "",
        "| Simvol | " + " | ".join(
            f"{b.label}: spread / illik faiz / komissiya" for b in brokers) + " |",
        "|---|" + "---|" * len(brokers),
    ]
    for symbol, per_broker in by_symbol.items():
        cells = []
        for broker in brokers:
            r = per_broker.get(broker.name)
            cells.append("—" if r is None else
                         f"{r['median_spread']:.3f} / {r['financing_pct']:.2f}% / "
                         + (f"${r['commission_per_lot']:.0f}/lot" if r["commission_per_lot"] else "yox"))
        out.append(f"| {symbol} | " + " | ".join(cells) + " |")
    out += [
        "",
        "Spread = bu pəncərədəki barların medianı, qiymət vahidində (R-ə təsiri birbaşa buradan "
        "gəlir). İllik faiz = bir lotun gecəlik swap-ı, həmin lotun dəyərinin faizi kimi — iki "
        "broker swap-ı fərqli vahidlərdə (illik % vs gecəlik $) yazdığı və kontrakt ölçüləri "
        "fərqli olduğu üçün xam dərəcələr müqayisə oluna bilməz, bu isə olunur.",
        "",
    ]
    return out


def notes_section(rows: list[dict], brokers: list[Broker]) -> list[str]:
    """Everything that would make a column in this report mean less than it looks."""
    capped = [r for r in rows if r["capped_pct"] > 1.0]
    out = ["## Oxunuşu məhdudlaşdıran şeylər", ""]
    if capped:
        out.append(
            "- **Lot tavanı.** Aşağıdakı botlar trade-lərinin bir hissəsində brokerin "
            "`volume_max` həddinə dirənir, yəni nəzərdə tutulan 0.5% riski ala bilmir — orada "
            "dollar sütunu zərəri olduğundan kiçik göstərir, **R sütunu isə düzgündür**:")
        for r in sorted(capped, key=lambda r: -r["capped_pct"]):
            out.append(f"  - `{r['task']}` @ {r['broker']}: trade-lərin {r['capped_pct']:.0f}%-i "
                       f"{r['volume_max']:g} lot tavanında, median risk {money(r['risk_median'])} "
                       f"(hədəf ~{money(0.005 * START_BALANCE)})")
    out += [
        "- **Swap bugünkü dərəcə ilə.** Hər iki brokerin swap-ı bu günkü dərəcədən bütün ilə "
        "tətbiq olunub; brokerlər tarixi dərəcələri yayımlamır.",
        "- **Tick datası istifadə olunmayıb.** CFI-də tick tarixçəsi yoxdur, ona görə "
        "FundingPips-inki də söndürülüb — qopma (gap) stopları hər iki tərəfdə eyni proksi "
        "ilə qiymətləndirilib. Bu, müqayisəni brokerə görə təmizləyir, amma hər iki tərəfdə "
        "stop qiymətini bir az optimist saya bilər.",
        f"- **Nümunə kiçikdir.** Bu pəncərədə bot başına {min(r['n'] for r in rows)}–"
        f"{max(r['n'] for r in rows)} bağlanmış trade var. Bu qədər trade-də PF-in təsadüfi "
        "sürüşməsi böyük olur — pəncərə nə qədər qısadırsa, nəticəni bir o qədər az ciddiyə "
        "almaq lazımdır.",
        f"- **Açıq qalanlar sayılmır.** Pəncərənin sonunda hələ açıq olan trade-lər statistikaya "
        f"girmir ({sum(r['open_trades'] for r in rows)} ədəd); 4R hədəflə mövqe günlərlə "
        "saxlanıldığı üçün qısa pəncərədə bu pay böyüyür.",
        "- **Bu portfel nəticəsi deyil.** Hər bot ayrıca "
        f"{money(START_BALANCE)}-da işlədilib; bir hesabda altısı birlikdə balansı və marja "
        "tavanını bölüşər, drawdown-lar isə üst-üstə düşər.",
        "",
    ]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brokers", default="fundingpips,cfi")
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--configs", default="", help="comma-separated task names; default all")
    parser.add_argument("--out", default="artifacts/orb_1y")
    parser.add_argument("--report", default="", help="report path; default names itself after --days")
    args = parser.parse_args()

    brokers = [BROKERS[name.strip()] for name in args.brokers.split(",") if name.strip()]
    wanted = {name.strip() for name in args.configs.split(",") if name.strip()}
    configs = [c for c in breakout_configs() if not wanted or c.task in wanted]
    out_dir = Path(args.out)

    start, end = window(brokers, configs, args.days)
    print(f"window {start:%Y-%m-%d} -> {end - timedelta(days=1):%Y-%m-%d}")
    rows: list[dict] = []
    for broker in brokers:
        print(f"--- {broker.label}")
        rows += summary_rows(replay(broker, configs, start, end, out_dir))

    report = Path(args.report) if args.report else report_path(args.days)
    report.write_text(render(rows, brokers, start, end, datetime.now(UTC)), encoding="utf-8")
    with (out_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {report} and {out_dir / 'summary.csv'}")


if __name__ == "__main__":
    main()
