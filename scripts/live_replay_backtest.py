"""Replay every deployed ORB bot the way the VPS runs it, beside the old batch backtests.

Usage:
    python -m scripts.live_replay_backtest
    python -m scripts.live_replay_backtest --configs OrbSweep_GER40_Demo --no-ablation
    python -m scripts.live_replay_backtest --data-dir data/history/fundingpips --out artifacts/live_replay

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
from backtest.live_replay.configs import BotConfig, scope
from backtest.live_replay.engine import Flags, TradeRecord, run
from backtest.live_replay.market import DEFAULT_DATA_DIR, BarFrame, load_fx, load_m1
from backtest.live_replay.metrics import equity_curve, stats, three_filters
from backtest.live_replay.specs import load_specs
from backtest.live_replay.ticks import TickCache
from scripts.two_strategy_symbol_sweep import recent_spread

REPORT_PATH = Path("LIVE_REPLAY_BACKTEST_REPORT.md")
ABLATIONS = ("poll_clock", "spread", "entry_ticks", "gap_ticks", "gap_proxy", "swap", "commission")
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


def old_rs(config: BotConfig, data_dir: Path) -> list[tuple[date, float]]:
    """The batch backtest's own trades, called exactly as scripts/live_vs_backtest_report.py does."""
    csv_path = Path(data_dir) / f"{config.symbol}_M1.csv"
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

    lines += ["", "Filtrlər sırası: tam tarixçə PF > 1, son 1 il PF > 1, 6 aylıq blokların ≥60%-i müsbət.",
              "", "## Fərqin parçalanması (netR, xüsusiyyət söndürüləndə)", "",
              "| Bot | tam əkiz | " + " | ".join(ABLATIONS) + " |",
              "|---|---|" + "---|" * len(ABLATIONS)]
    for result in results:
        full = stats([r for _, r in _dated(result.trades)]).net_r
        cells = " | ".join(f"{result.ablation[name]:+.1f}" if name in result.ablation else "—"
                           for name in ABLATIONS)
        lines.append(f"| {result.config.task} | {full:+.1f} | {cells} |")

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

    lines += [
        "", "## Məhdudiyyətlər", "",
        "- Swap dərəcələri tarixi deyil: bütün tarixçəyə 2026-09-15 dərəcələri tətbiq olunub.",
        "- Spread hər M1 barın öz spread sütunundandır; tick müqayisəsi yuxarıdakı nisbətdədir.",
        "- Tick tarixçəsi indekslərdə 2025-03, qızılda 2026-05-dən başlayır; ondan əvvəlki boşluq "
        "stopları bar close proksisi ilə qiymətləndirilib (`gap_proxy` sütunu bunun qiymətidir).",
        "- Poll saniyəsi sabit götürülüb; real jitter 4–6 saniyədir.",
        "- Requote, reject, AutoTrading kəsintiləri və FundingPips-in məcburi bağlanışları modelləşdirilmir.",
        "- Botlarda heç nə dəyişmir; 2026-10-12 dondurma planı qüvvədədir.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--configs", default="", help="comma-separated task names; default all ten")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--out", default="artifacts/live_replay")
    parser.add_argument("--no-ablation", action="store_true")
    parser.add_argument("--no-old", action="store_true")
    parser.add_argument("--validation-note", default="artifacts/live_replay/validation.md")
    args = parser.parse_args()

    data_dir, out_dir = Path(args.data_dir), Path(args.out)
    wanted = {name.strip() for name in args.configs.split(",") if name.strip()}
    configs = [c for c in scope() if not wanted or c.task in wanted]
    specs = load_specs()
    ticks = TickCache(data_dir / "ticks")
    results: list[ConfigResult] = []
    end = date.min  # "last year" is measured back from the newest bar, not from today

    for config in configs:
        print(f"--- {config.task}")
        spec = specs[config.symbol]
        m1 = load_m1(config.symbol, data_dir)  # ~150MB: load once, replay it seven times
        fx = load_fx(spec.profit_currency, data_dir)
        trades = run(config, m1, spec, fx, ticks=ticks)
        end = max(end, datetime.fromtimestamp(int(m1.ts[-1]), UTC).date())
        ablation: dict[str, float] = {}
        if not args.no_ablation:
            for name in ABLATIONS:
                without = run(config, m1, spec, fx, ticks=ticks, flags=Flags(**{name: False}))
                ablation[name] = stats([t.r for t in without if t.exit_reason != "OPEN"]).net_r
        old = [] if args.no_old else old_rs(config, data_dir)
        recorded = RECORDED_OLD_PF.get(config.task)
        reproduced = (stats([r for day, r in old if day <= RECORDED_OLD_END]).pf
                      if old and recorded is not None else None)
        results.append(ConfigResult(config=config, trades=trades, old=old, ablation=ablation,
                                    spread_ratio=spread_ratio(trades, m1, ticks),
                                    recorded_pf=recorded, reproduced_pf=reproduced))
        write_trades_csv(out_dir / f"{config.task}_trades.csv", trades)

    note_path = Path(args.validation_note)
    note = note_path.read_text(encoding="utf-8") if note_path.exists() else "(testlər işlədilməyib)"
    REPORT_PATH.write_text(render_report(results, end, datetime.now(UTC), note), encoding="utf-8")
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
