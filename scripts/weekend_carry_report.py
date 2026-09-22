#!/usr/bin/env python
"""Which replayed trades were carried over a weekend, and what that carry cost.

Usage:
    python -m scripts.weekend_carry_report
    python -m scripts.weekend_carry_report --configs OrbBreakout_NDX100_Demo
    python -m scripts.weekend_carry_report --data-dir data/history/fundingpips

Writes WEEKEND_CARRY_REPORT.md and artifacts/live_replay/weekend_carry.csv. It has its own report
file rather than a section in LIVE_REPLAY_BACKTEST_REPORT.md because that one is rewritten whole by
scripts/live_replay_backtest.py on every run, which would drop anything added here.

The replay already models a weekend hold: engine/reversal walk M1 bars with no time bound, so a
trade open at Friday's last bar continues on Monday's first, and a stop hit on that bar is priced
as a post-break gap (SL_GAP_TICK from real ticks, else SL_GAP_PROXY, else SL_GAP_LEVEL). Swap is
charged at every server midnight crossed, including the Friday triple for the index CFDs. Nothing
here changes the engine -- it only labels finished trades, splitting the --reverse-on-stop legs
(setup_id suffix `_sar`) from the breakout entries that spawned them.

Read the breakout comparison with care: it is confounded and the report says so. A trade only
reaches Friday's close if it has not stopped yet, and the breakout targets 4R, so its winners run
for days while its losers stop out fast. "Carried" therefore selects for eventual winners, and the
carried-vs-not gap measures survival time rather than the weekend. The reverse leg targets 0.5R and
resolves in hours, so that same comparison is far less contaminated -- which is why it is the one
the report leads with. The clean test for the breakout is the --weekend-flat A/B, which the engine
refuses to model together with --reverse-on-stop.
"""

from __future__ import annotations

import argparse
import statistics as st
import sys
from collections import Counter
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

import pandas as pd

from backtest.live_replay.brokers import BROKERS, connected_server, tick_cache
from backtest.live_replay.configs import BotConfig, scope
from backtest.live_replay.engine import TradeRecord, run
from backtest.live_replay.market import DEFAULT_DATA_DIR, load_fx, load_m1
from backtest.live_replay.reversal import REVERSE_SUFFIX
from backtest.live_replay.specs import load_specs
from core.broker_clock import BrokerClock

REPORT_PATH = Path("WEEKEND_CARRY_REPORT.md")


@dataclass(frozen=True)
class Scored:
    """One replayed trade, labelled with the two things this report splits on."""

    config: BotConfig
    trade: TradeRecord
    leg: str       # "reverse" (the --reverse-on-stop leg) | "breakout" (the entry that spawned it)
    carried: bool  # the hold spans a Saturday in broker time


def spans_weekend(trade: TradeRecord, clock: BrokerClock) -> bool:
    """True when a Saturday in broker time (`clock`, the server's) falls inside the hold."""
    a = trade.entry_time.astimezone(clock).replace(tzinfo=None)
    b = trade.exit_time.astimezone(clock).replace(tzinfo=None)
    day = datetime.combine(a.date(), time())
    while day <= b:
        if day.weekday() == 5 and day > a:
            return True
        day += timedelta(days=1)
    return False


def welch_t(a: list[float], b: list[float]) -> float:
    """Welch's t for `a` minus `b`; 0.0 when either side is too small to have a variance."""
    if len(a) < 2 or len(b) < 2:
        return 0.0
    se = (st.variance(a) / len(a) + st.variance(b) / len(b)) ** 0.5
    return (st.mean(a) - st.mean(b)) / se if se > 0 else 0.0


def score(configs: list[BotConfig], data_dir: Path) -> tuple[list[Scored], dict[str, str]]:
    """Replays each config, labels every trade, and records each symbol's M1 span.

    The span is the bar data's, not the trades' -- the report's limitations point at it to judge
    how deep each symbol's history actually goes, and the first trade can post-date the first bar
    by years.
    """
    # FundingPips' data, so its ticks are only fetched while MT5 is logged into FundingPips:
    # a miss fetched from any other account would be cached as "no ticks" for good.
    fundingpips = replace(BROKERS["fundingpips"], data_dir=Path(data_dir))
    specs, ticks = load_specs(), tick_cache(fundingpips, connected_server())
    out: list[Scored] = []
    spans: dict[str, str] = {}
    for config in configs:
        spec = specs[config.symbol]
        m1 = load_m1(config.symbol, data_dir)
        first, last = pd.Timestamp(m1.ts[0], unit="s"), pd.Timestamp(m1.ts[-1], unit="s")
        spans[config.symbol] = f"{first:%Y-%m}..{last:%Y-%m}"
        fx = load_fx(spec.profit_currency, data_dir)
        trades = run(config, m1, spec, fx, ticks=ticks)
        for t in trades:
            leg = "reverse" if t.setup_id.endswith(REVERSE_SUFFIX) else "breakout"
            out.append(Scored(config, t, leg, spans_weekend(t, m1.clock)))
        carried = sum(1 for s in out if s.config is config and s.carried)
        print(f"--- {config.task:<28} trades {len(trades):>5}  carried {carried:>4}", flush=True)
    return out, spans


def _stats(rows: list[Scored]) -> dict:
    rs = [s.trade.r for s in rows]
    gaps = [s.trade.r for s in rows if s.trade.exit_reason.startswith("SL_GAP")]
    return {
        "n": len(rs), "net": sum(rs), "mean": st.mean(rs) if rs else 0.0,
        "median": st.median(rs) if rs else 0.0,
        "win": sum(1 for r in rs if r > 0) / len(rs) * 100 if rs else 0.0,
        "gaps": len(gaps), "gap_rate": len(gaps) / len(rs) * 100 if rs else 0.0,
        "gap_mean": st.mean(gaps) if gaps else float("nan"),
    }


def render(scored: list[Scored], configs: list[BotConfig], spans: dict[str, str],
           built: datetime) -> str:
    """The report. Every number in it comes from `scored`; nothing is written by hand."""
    lines = [
        "# Həftə sonu daşıması — canlı əkiz replay",
        "",
        f"Hazırlandı: {built:%Y-%m-%d %H:%M} UTC · generasiya edən: "
        "`scripts/weekend_carry_report.py`",
        "",
        "Sual: `--reverse-on-stop` ayağını həftə sonu saxlamaq nəyə başa gəlir? Mühərrikdə heç nə",
        "dəyişmir — replay onsuz da həftə sonu saxlamağı modelləşdirir (M1 barları zaman limiti",
        "olmadan gəzilir, boşluq stopları `SL_GAP_TICK`/`SL_GAP_PROXY` ilə qiymətlənir, swap hər",
        "server gecəyarısında, indekslərin cümə üçqatı daxil). Bu skript yalnız bitmiş işlemləri",
        "etiketləyir.",
        "",
        "## Konfiqurasiyalar",
        "",
        "| Bot | Simvol | reverse_on_stop | M1 barları | işlem | ters | daşınan |",
        "|---|---|---|---|---|---|---|",
    ]
    for config in configs:
        rows = [s for s in scored if s.config is config]
        if not rows:
            continue
        rev = sum(1 for s in rows if s.leg == "reverse")
        lines.append(
            f"| {config.task} | {config.symbol} | {config.reverse_on_stop_r or '—'} | "
            f"{spans.get(config.symbol, '—')} | {len(rows)} | {rev} | "
            f"{sum(1 for s in rows if s.carried)} |"
        )

    lines += ["", "## Daşınan vs daşınmayan, ayaq üzrə", "",
              "| Ayaq | Daşınma | n | net R | orta | median | uduş % |",
              "|---|---|---|---|---|---|---|"]
    verdicts = {}
    for leg in ("reverse", "breakout"):
        legs = [s for s in scored if s.leg == leg]
        yes = [s for s in legs if s.carried]
        no = [s for s in legs if not s.carried]
        for label, rows in (("daşınan", yes), ("daşınmayan", no)):
            k = _stats(rows)
            lines.append(f"| {leg} | {label} | {k['n']} | {k['net']:+.2f} | {k['mean']:+.3f} | "
                         f"{k['median']:+.3f} | {k['win']:.2f} |")
        t = welch_t([s.trade.r for s in yes], [s.trade.r for s in no])
        diff = _stats(yes)["mean"] - _stats(no)["mean"]
        verdicts[leg] = (diff, t)
        lines.append(f"| {leg} | **fərq** | | | **{diff:+.3f}** | | **t = {t:+.2f}** |")

    lines += ["", "## Boşluq stopu — mexanizm", "",
              "Həftə sonunu keçmək boşluq stopuna düşmə ehtimalını neçə dəfə artırır:", "",
              "| Ayaq | Daşınma | n | boşluq stopu | tezlik | onların ortası |",
              "|---|---|---|---|---|---|"]
    for leg in ("reverse", "breakout"):
        for label, want in (("daşınan", True), ("daşınmayan", False)):
            k = _stats([s for s in scored if s.leg == leg and s.carried is want])
            mean = "—" if k["gaps"] == 0 else f"{k['gap_mean']:+.2f} R"
            lines.append(f"| {leg} | {label} | {k['n']} | {k['gaps']} | "
                         f"{k['gap_rate']:.2f}% | {mean} |")

    gaps = [s.trade for s in scored if s.carried and s.trade.exit_reason.startswith("SL_GAP")]
    if gaps:
        tick = [t.r for t in gaps if t.exit_reason == "SL_GAP_TICK"]
        proxy = [t.r for t in gaps if t.exit_reason == "SL_GAP_PROXY"]
        worse = sum(1 for t in gaps if t.r < -1)
        lines += ["", "Daşınan işlemlərin boşluq stopları, qiymətləmə mənbəyinə görə:", ""]
        sources = (("`SL_GAP_TICK` (real tick)", tick), ("`SL_GAP_PROXY` (bar-close)", proxy))
        for label, rs in sources:
            if rs:
                lines.append(f"- {label}: n={len(rs)}, orta **{st.mean(rs):+.3f} R**, "
                             f"median {st.median(rs):+.3f} R, ən pis {min(rs):+.3f} R")
        if tick and proxy and st.mean(tick) < st.mean(proxy):
            lines.append("- Real tick ilə qiymətlənənlər proksidən pisdir, yəni köhnə data "
                         "üzərindəki proksi boşluq riskini **az göstərir**.")
        lines.append(f"- Boşluq stoplarının **{worse}/{len(gaps)}**-i (−1R)-dən pisdir.")

    rev = [s.trade.r for s in scored if s.leg == "reverse"]
    reward = next((c.reverse_on_stop_r for c in configs if c.reverse_on_stop_r), None)
    if rev and reward:
        win = sum(1 for r in rev if r > 0) / len(rev) * 100
        need = 100 / (1 + reward)
        implied = win / 100 * reward - (1 - win / 100)
        lines += [
            "", f"## `--reverse-on-stop {reward}` qaydasının özü", "",
            "Həftə sonundan asılı olmayaraq, bütün ters ayaqlar:", "",
            f"- n = **{len(rev)}**, net **{sum(rev):+.1f} R**, orta **{st.mean(rev):+.4f} R**",
            f"- uduş **{win:.2f}%**, 1R risk / {reward}R hədəf üçün sıfır nöqtəsi **{need:.2f}%** "
            f"→ **{need - win:.2f} punkt** çatmır",
            f"- yalnız uduş nisbətindən çıxan gözlənti **{implied:+.4f} R** — müşahidə olunan "
            f"{st.mean(rev):+.4f} R ilə demək olar eyni",
            "",
            "Yəni çatışmazlıq giriş keyfiyyətində deyil, **ödəniş həndəsəsindədir**: ters ayaq tam "
            f"stop məsafəsini riskə atıb onun {reward} hissəsini hədəfləyir.",
        ]

    diff, t = verdicts.get("breakout", (0.0, 0.0))
    lines += [
        "", "## Məhdudiyyətlər", "",
        f"- Breakout sətri ({diff:+.3f} R, t {t:+.2f}) **səbəbiyyət deyil**. Breakout 4R "
        "hədəfləyir: "
        "uduzanlar tez stop olur, udanlar günlərlə qaçır, ona görə cümə bağlanışına çatmaq "
        "gələcək qalibləri seçir. O rəqəm yaşama müddətini ölçür, həftə sonunu yox.",
        "- Ters ayaq 0.5R hədəfləyib saatlarla həll olunduğu üçün eyni qərəz orada xeyli zəifdir — "
        "hesabatın əsas nəticəsi ona görə ters sətridir.",
        "- Breakout üçün təmiz test `--weekend-flat` A/B-dir; mühərrik onu `--reverse-on-stop` ilə "
        "birgə modelləşdirməkdən imtina edir (`backtest/live_replay/engine.py`).",
        "- Simvolların tarixçə dərinliyi çox fərqlidir (yuxarıdakı cədvələ bax), ona görə "
        "per-simvol nümunələr bir-biri ilə müqayisə oluna bilməz.",
        "- Tick tarixçəsi indekslərdə 2025-03, qızılda 2026-05-dən başlayır; ondan əvvəlki boşluq "
        "stopları bar-close proksisi ilə qiymətlənib.",
        "- M1 tarixçəsi MT5-in `Max bars in chart` ayarından asılıdır; default 100000 onu ~3 aya "
        "kəsir və nümunəni sükutla kiçildir.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Weekend-carry report for the live replay.")
    parser.add_argument("--configs", default="", help="comma-separated task names; default all")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--out", default="artifacts/live_replay/weekend_carry.csv")
    args = parser.parse_args()

    wanted = {n.strip() for n in args.configs.split(",") if n.strip()}
    configs = [c for c in scope() if not wanted or c.task in wanted]
    scored, spans = score(configs, Path(args.data_dir))

    carried = [s for s in scored if s.carried]
    print(f"\n{len(carried)} weekend-carried trades, "
          f"of which {sum(1 for s in carried if s.leg == 'reverse')} are reverse legs")
    for leg in ("breakout", "reverse"):
        rows = [s for s in carried if s.leg == leg]
        if rows:
            print(f"  {leg:<9} {dict(Counter(s.trade.exit_reason for s in rows))}  "
                  f"net R {sum(s.trade.r for s in rows):+.2f}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{
        "task": s.config.task, "symbol": s.trade.symbol, "leg": s.leg,
        "direction": s.trade.direction, "entry_time": s.trade.entry_time,
        "exit_time": s.trade.exit_time, "entry": s.trade.entry, "stop": s.trade.stop,
        "exit": s.trade.exit, "exit_reason": s.trade.exit_reason, "r": round(s.trade.r, 3),
        "pnl_usd": round(s.trade.pnl_usd, 2), "swap_usd": round(s.trade.swap_usd, 2),
    } for s in carried]).to_csv(out, index=False)
    REPORT_PATH.write_text(render(scored, configs, spans, datetime.now(UTC)), encoding="utf-8")
    print(f"wrote {out} and {REPORT_PATH}")


if __name__ == "__main__":
    main()
