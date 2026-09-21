"""Replay every deployed ORB bot the way the VPS runs it, beside the old batch backtests.

Each bot is replayed on the broker its launcher names (backtest/live_replay/brokers.py): that
broker's history, trimmed to its real minute data, its contract specs, FX files and tick cache.

Usage:
    python -m scripts.live_replay_backtest
    python -m scripts.live_replay_backtest --configs OrbSweep_GER40_Demo --no-ablation
    python -m scripts.live_replay_backtest --data-dir data/history/fundingpips --out artifacts/live_replay
      (--data-dir forces one folder, named and specced as FundingPips, for every bot)

Writes artifacts/live_replay/<task>_trades.csv and LIVE_REPLAY_BACKTEST_REPORT.md.
Read the spec before changing any of this: docs/superpowers/specs/2026-09-15-live-replay-backtest-design.md
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.resolve()))

import numpy as np

import scripts.nasdaq_orb_m1_breakout_backtest as orb_mod
import scripts.xauusd_orb_liquidity_sweep_backtest as sweep_mod
from backtest.live_replay.brokers import (
    BROKERS, Broker, broker_for, connected_server, history_path, tick_cache,
)
from backtest.live_replay.configs import BotConfig, scope
from backtest.live_replay.engine import Flags, TradeRecord, run
from backtest.live_replay.market import BarFrame, load_bars, load_fx, trim_to_real_m1
from backtest.live_replay.metrics import equity_curve, stats, three_filters
from backtest.live_replay.specs import load_specs
from backtest.live_replay.ticks import TickCache
from scripts.two_strategy_symbol_sweep import recent_spread

REPORT_PATH = Path("LIVE_REPLAY_BACKTEST_REPORT.md")
ABLATIONS = ("poll_clock", "spread", "entry_ticks", "gap_ticks", "gap_proxy", "swap", "commission")
SPREAD_RATIO_LIMIT = 1.10  # spec §6 G4: above this the bar spread understates real ticks; rerun scaled
# PFs this repo already recorded for these exact configurations, on data ending ~2026-09-08
# (deploy/demo_roster.txt comments, WALK_FORWARD_2026_09_09_REPORT.md). The old column is only
# trustworthy if it still reproduces them.
RECORDED_OLD_END = date(2026, 9, 8)
RECORDED_OLD_PF = {
    "OrbBreakout_XAUUSD_Demo": 1.235, "OrbBreakout_NDX100_Demo": 1.329,
    "OrbBreakout_SPX500_Demo": 1.326, "OrbBreakout_DJI30_Demo": 1.205,
    "OrbBreakout_JP225_Demo": 1.177, "OrbSweep_GER40_Demo": 1.547,
    "OrbBreakout_XAUUSD_Paper": 1.389, "OrbSweep_JP225_Paper": 1.253,
}


@dataclass
class ConfigResult:
    config: BotConfig
    trades: list[TradeRecord]          # every replayed trade, OPEN included
    old: list[tuple[date, float]]
    ablation: dict[str, float]         # feature switched off -> net R without it
    spread_ratio: float | None
    recorded_pf: float | None
    reproduced_pf: float | None
    spread_sensitivity: tuple[float, float] | None = None  # (PF, net R) at spread_scale = spread_ratio


def old_rs(config: BotConfig, csv_path: Path) -> list[tuple[date, float]]:
    """The batch backtest's own trades on the same history file the replay reads."""
    spread = recent_spread(csv_path)
    if config.family == "breakout":
        trades = orb_mod.run_backtest(str(csv_path), "full", spread, config.tp_r, "long",
                                      or_minutes=config.or_minutes, scan_minutes=config.scan_minutes)
    else:
        hour, minute = ((int(p) for p in config.entry_window_end.split(":"))
                        if config.entry_window_end else (11, 0))
        trades, _ = sweep_mod.run_backtest(str(csv_path), tp_r=2.0, spread_points=spread,
                                           enable_breakout=False, bar_minutes=15,
                                           entry_window_end=time(hour, minute),
                                           entry_fill_mode="next_open")
    return [(date.fromisoformat(str(t.day)[:10]), t.r_multiple) for t in trades]


def spread_ratio(trades: list[TradeRecord], m1: BarFrame, ticks: TickCache) -> float | None:
    """Median (tick spread / bar spread) at the entries the broker still has ticks for."""
    ratios: list[float] = []
    for trade in trades:
        start = int(trade.entry_time.timestamp())
        if not ticks.has_history(trade.symbol, start):
            continue
        rows = ticks.window(trade.symbol, start, 60)
        index = int(np.searchsorted(m1.ts, start))
        if not rows or index >= len(m1) or m1.spread[index] <= 0:
            continue
        ratios.append(statistics.median(ask - bid for _, bid, ask in rows) / float(m1.spread[index]))
    return statistics.median(ratios) if ratios else None


def write_trades_csv(path: Path, trades: list[TradeRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(trades[0]).keys()) if trades else ["setup_id"])
        writer.writeheader()
        for trade in trades:
            writer.writerow(asdict(trade))


def _dated(trades: list[TradeRecord]) -> list[tuple[date, float]]:
    return [(t.entry_time.date(), t.r) for t in trades if t.exit_reason != "OPEN"]


def _fmt(value: float) -> str:
    return "inf" if value == float("inf") else f"{value:.3f}"


def render_report(results: list[ConfigResult], end: date, generated: datetime,
                  validation_note: str) -> str:
    lines = [
        "# Canlı Əkiz Backtest — nəticə",
        "",
        f"Hazırlandı: {generated:%Y-%m-%d %H:%M} UTC · data sonu: {end:%Y-%m-%d} · "
        "spec: `docs/superpowers/specs/2026-09-15-live-replay-backtest-design.md`",
        "",
        "## Yoxlamalar",
        "",
        validation_note,
        "",
        "## Əsas cədvəl (köhnə backtest vs canlı əkiz)",
        "",
        "| Bot | köhnə n | köhnə PF | köhnə netR | əkiz n | əkiz PF | əkiz netR | əkiz maxDD R | "
        "son 1 il PF (köhnə → əkiz) | 6 aylıq blok yaşıl % | filtrlər |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for result in results:
        mine = _dated(result.trades)
        old_stats, new_stats = stats([r for _, r in result.old]), stats([r for _, r in mine])
        old_filters, new_filters = three_filters(result.old, end), three_filters(mine, end)
        marks = ("✅" if new_filters.full_ok else "❌") + ("✅" if new_filters.last_year_ok else "❌") \
            + ("✅" if new_filters.blocks_ok else "❌") + (" ⚠️az tarixçə" if new_filters.short_history else "")
        lines.append(
            f"| {result.config.task} | {old_stats.n} | {_fmt(old_stats.pf)} | {old_stats.net_r:+.1f} | "
            f"{new_stats.n} | {_fmt(new_stats.pf)} | {new_stats.net_r:+.1f} | {new_stats.max_dd_r:.1f} | "
            f"{_fmt(old_filters.last_year_pf)} → {_fmt(new_filters.last_year_pf)} | "
            f"{new_filters.blocks_green_pct:.0f}% ({new_filters.blocks}) | {marks} |")

    if any(result.config.weekend_flat for result in results):
        lines += ["", "Həftə sonu bağlayan botlarda (`--weekend-flat`) köhnə sütun batch backtest-dir və "
                      "həftə sonu qaydasını bilmir: orada eyni konfiqurasiyanın mövqe saxlayan versiyası göstərilir."]
    if any(result.config.reverse_on_stop_r is not None for result in results):
        lines += ["", "Stopdan sonra əks trade açan botlarda (`--reverse-on-stop`) əkiz sütunu reversal trade-ləri "
                      "də sayır (setup_id `_sar` ilə bitir), köhnə sütun isə reversal-sız batch backtest-dir."]
    if any(result.config.inverse for result in results):
        lines += ["", "Tərs botlarda (`--inverse`) köhnə sütun tərs olmayan orijinal strategiyanın batch "
                      "backtest-idir; müqayisə üçün yox, yalnız istinad üçündür."]
    lines += ["", "Filtrlər sırası: tam tarixçə PF > 1, son 1 il PF > 1, 6 aylıq blokların ≥60%-i müsbət.",
              "", "## Fərqin parçalanması (netR, xüsusiyyət söndürüləndə)", "",
              "| Bot | tam əkiz | " + " | ".join(ABLATIONS) + " |",
              "|---|---|" + "---|" * len(ABLATIONS)]
    for result in results:
        full = stats([r for _, r in _dated(result.trades)]).net_r
        cells = " | ".join(f"{result.ablation[name]:+.1f}" if name in result.ablation else "—"
                           for name in ABLATIONS)
        lines.append(f"| {result.config.task} | {full:+.1f} | {cells} |")

    lines += ["", "**Swap sütununu necə oxumaq lazımdır.** Ən böyük fərq adətən swap-dandır: bu strategiya "
              "4R hədəflə günlərlə mövqe saxlayır, indekslərdə isə illik 7.33% maliyyələşdirmə tutulur. Amma "
              "bütün tarixçəyə **bugünkü** swap dərəcəsi tətbiq olunub; 2020–2021-də faizlər sıfıra yaxın idi, "
              "deməli o illərin real swap xərci xeyli az olub. Düzgün oxunuş: **həqiqi nəticə \"tam əkiz\" ilə "
              "\"swap\" sütununun arasındadır** — birincisi bugünkü dərəcə, ikincisi sıfır faiz sərhədi. "
              "Brokerin tarixi swap dərəcələri saxlanmadığı üçün daha dəqiq hesablamaq olmur."]
    lines += ["", "## $50,000 hesabda (hər bot ayrıca)", "",
              "| Bot | son balans | max drawdown % | trade | swap $ | komissiya $ |",
              "|---|---|---|---|---|---|"]
    for result in results:
        closed = [t for t in result.trades if t.exit_reason != "OPEN"]
        final, drawdown = equity_curve([t.pnl_usd for t in closed], 50_000.0)
        lines.append(f"| {result.config.task} | ${final:,.0f} | {drawdown:.1f}% | {len(closed)} | "
                     f"${sum(t.swap_usd for t in closed):,.0f} | "
                     f"${sum(t.commission_usd for t in closed):,.0f} |")

    lines += ["", "## Çıxış növləri və köhnə sütunun yoxlanması", "",
              "| Bot | TP | SL | boşluq (tick) | boşluq (proksi) | açıq | eyni bar SL+TP | "
              "giriş barında bağlanan | spread nisbəti | qeydə alınmış köhnə PF | təkrar |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for result in results:
        reasons = [t.exit_reason for t in result.trades]
        ratio = "—" if result.spread_ratio is None else f"{result.spread_ratio:.2f}x"
        recorded = "—" if result.recorded_pf is None else f"{result.recorded_pf:.3f}"
        reproduced = "—" if result.reproduced_pf is None else f"{result.reproduced_pf:.3f}"
        lines.append(
            f"| {result.config.task} | {reasons.count('TP')} | {reasons.count('SL')} | "
            f"{reasons.count('SL_GAP_TICK')} | {reasons.count('SL_GAP_PROXY') + reasons.count('SL_GAP_LEVEL')} | "
            f"{reasons.count('OPEN')} | {sum(1 for t in result.trades if t.both_levels_touched)} | "
            f"{sum(1 for t in result.trades if t.closed_on_entry_bar)} | {ratio} | {recorded} | {reproduced} |")

    flagged = [r for r in results if r.spread_ratio is not None and r.spread_ratio > SPREAD_RATIO_LIMIT]
    if not flagged:
        lines += ["", "**Spread yoxlaması (G4).** Bar-ların spread sütunu bütün simvollarda real tick "
                      "spread-i ilə uyğundur."]
    else:
        lines += ["", f"**Spread yoxlaması (G4).** Aşağıdakı konfiqurasiyalarda bar spread-i real tick "
                      f"spread-indən {round((SPREAD_RATIO_LIMIT - 1) * 100)}%-dən çox aşağıdır; onlar tick "
                      "nisbəti ilə yenidən hesablandı (yuxarıdakı cədvəllər 1.00x ilədir):"]
        for result in flagged:
            if result.spread_sensitivity is None:
                continue
            base = stats([r for _, r in _dated(result.trades)])
            pf, net_r = result.spread_sensitivity
            lines.append(f"- {result.config.task}: {result.spread_ratio:.2f}x ilə PF {_fmt(base.pf)} → "
                         f"{_fmt(pf)}, net R {base.net_r:+.1f} → {net_r:+.1f}")

    lines += [
        "", "## Məhdudiyyətlər", "",
        "- Swap dərəcələri tarixi deyil: bütün tarixçəyə hər brokerin spec faylının capture "
        "tarixindəki dərəcələri tətbiq olunub (FundingPips 2026-09-15, CFI 2026-09-20).",
        "- Hər bot öz brokerinin datasında replay olunur, həmin brokerin real M1 datası "
        "başlayandan (CFI qızılı 2017-dən).",
        "- Spread hər M1 barın öz spread sütunundandır; tick müqayisəsi yuxarıdakı nisbətdədir.",
        "- Tick tarixçəsi indekslərdə 2025-03, qızılda 2026-05-dən başlayır; ondan əvvəlki boşluq "
        "stopları bar close proksisi ilə qiymətləndirilib (`gap_proxy` sütunu bunun qiymətidir).",
        "- Poll saniyəsi sabit götürülüb; real jitter 4–6 saniyədir.",
        "- Requote, reject, AutoTrading kəsintiləri və brokerin məcburi bağlanışları modelləşdirilmir.",
        "- Botlarda heç nə dəyişmir; 2026-10-12 dondurma planı qüvvədədir.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs", default="", help="comma-separated task names; default all ten")
    parser.add_argument("--data-dir", default="",
                        help="replay every bot on this one folder with FundingPips specs "
                             "(default: each bot on its own broker's)")
    parser.add_argument("--out", default="artifacts/live_replay")
    parser.add_argument("--no-ablation", action="store_true")
    parser.add_argument("--no-old", action="store_true")
    parser.add_argument("--validation-note", default="artifacts/live_replay/validation.md")
    args = parser.parse_args()

    out_dir = Path(args.out)
    wanted = {name.strip() for name in args.configs.split(",") if name.strip()}
    configs = [c for c in scope() if not wanted or c.task in wanted]
    forced = (Broker("forced", args.data_dir, Path(args.data_dir), BROKERS["fundingpips"].specs_file)
              if args.data_dir else None)
    logged_into = connected_server()
    caches: dict[str, TickCache] = {}
    results: list[ConfigResult] = []
    end = date.min  # "last year" is measured back from the newest bar, not from today

    for config in configs:
        broker = forced or broker_for(config)
        print(f"--- {config.task}  ({broker.name})")
        spec = load_specs(broker.specs_file)[config.symbol]
        csv_path = history_path(broker, spec)
        # ~150MB: load once, replay it seven times. Trimmed, or CFI's pre-2017 gold -- a few
        # hundred coarse rows a year -- would be replayed as minute bars.
        m1 = trim_to_real_m1(load_bars(csv_path, config.symbol, 1))
        fx = load_fx(spec.profit_currency, broker.data_dir)
        ticks = caches.setdefault(broker.name, tick_cache(broker, logged_into))
        trades = run(config, m1, spec, fx, ticks=ticks)
        end = max(end, datetime.fromtimestamp(int(m1.ts[-1]), UTC).date())
        ablation: dict[str, float] = {}
        if not args.no_ablation:
            for name in ABLATIONS:
                without = run(config, m1, spec, fx, ticks=ticks, flags=Flags(**{name: False}))
                ablation[name] = stats([t.r for t in without if t.exit_reason != "OPEN"]).net_r
        # The batch backtest reads the whole CSV itself, padding included; its trades are dated,
        # so the ones it took on non-minute rows are dropped here.
        real_from = datetime.fromtimestamp(int(m1.ts[0]), UTC).date()
        old = [] if args.no_old else [(d, r) for d, r in old_rs(config, csv_path) if d >= real_from]
        recorded = RECORDED_OLD_PF.get(config.task)
        reproduced = (stats([r for day, r in old if day <= RECORDED_OLD_END]).pf
                      if old and recorded is not None else None)
        ratio = spread_ratio(trades, m1, ticks)
        sensitivity = None
        if ratio is not None and ratio > SPREAD_RATIO_LIMIT:
            scaled = stats([t.r for t in run(config, m1, spec, fx, ticks=ticks, spread_scale=ratio)
                            if t.exit_reason != "OPEN"])
            sensitivity = (scaled.pf, scaled.net_r)
        results.append(ConfigResult(config=config, trades=trades, old=old, ablation=ablation,
                                    spread_ratio=ratio, recorded_pf=recorded, reproduced_pf=reproduced,
                                    spread_sensitivity=sensitivity))
        write_trades_csv(out_dir / f"{config.task}_trades.csv", trades)

    note_path = Path(args.validation_note)
    note = note_path.read_text(encoding="utf-8") if note_path.exists() else "(testlər işlədilməyib)"
    REPORT_PATH.write_text(render_report(results, end, datetime.now(UTC), note), encoding="utf-8")
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
