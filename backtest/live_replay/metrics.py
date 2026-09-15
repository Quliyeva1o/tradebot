"""The numbers the report prints.

The deployment rule this project uses is: full-history PF > 1, last-year PF > 1, and a
consistent record across periods. The third leg was originally a walk-forward fold record,
which measures a SELECTION process; a fixed configuration has nothing to select, so the
measurable equivalent is the share of calendar half-years that end positive. The report says
so rather than presenting the two as the same test.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

SHORT_HISTORY_DAYS = 913  # 30 months: a 24+6 walk-forward fold does not fit in less


@dataclass(frozen=True)
class Stats:
    n: int
    win_pct: float
    pf: float
    net_r: float
    avg_r: float
    max_dd_r: float
    worst_streak: int


def stats(rs: Sequence[float]) -> Stats:
    if not rs:
        return Stats(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)
    wins = [r for r in rs if r > 0]
    losses = -sum(r for r in rs if r <= 0)
    peak = cum = drawdown = 0.0
    streak = worst = 0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        drawdown = max(drawdown, peak - cum)
        streak = streak + 1 if r <= 0 else 0
        worst = max(worst, streak)
    return Stats(n=len(rs), win_pct=100.0 * len(wins) / len(rs),
                 pf=(sum(wins) / losses) if losses > 0 else float("inf"),
                 net_r=sum(rs), avg_r=sum(rs) / len(rs), max_dd_r=drawdown, worst_streak=worst)


def since(dated: Sequence[tuple[date, float]], start: date | None) -> list[float]:
    return [r for day, r in dated if start is None or day >= start]


def half_year_blocks(dated: Sequence[tuple[date, float]]) -> dict[str, list[float]]:
    blocks: dict[str, list[float]] = defaultdict(list)
    for day, r in dated:
        blocks[f"{day.year}H{1 if day.month <= 6 else 2}"].append(r)
    return dict(sorted(blocks.items()))


@dataclass(frozen=True)
class FilterResult:
    full_pf: float
    last_year_pf: float
    blocks_green_pct: float
    blocks: int
    full_ok: bool
    last_year_ok: bool
    blocks_ok: bool
    short_history: bool

    @property
    def passed(self) -> bool:
        return self.full_ok and self.last_year_ok and self.blocks_ok


def three_filters(dated: Sequence[tuple[date, float]], end: date) -> FilterResult:
    full = stats([r for _, r in dated]).pf
    last_year = stats(since(dated, end - timedelta(days=365))).pf
    blocks = half_year_blocks(dated)
    green = [sum(rs) > 0 for rs in blocks.values()]
    green_pct = 100.0 * sum(green) / len(green) if green else 0.0
    first = min((day for day, _ in dated), default=end)
    return FilterResult(full_pf=full, last_year_pf=last_year, blocks_green_pct=green_pct,
                        blocks=len(blocks), full_ok=full > 1.0, last_year_ok=last_year > 1.0,
                        blocks_ok=green_pct >= 60.0,
                        short_history=(end - first).days < SHORT_HISTORY_DAYS)


def equity_curve(pnls_usd: Sequence[float], start_balance: float) -> tuple[float, float]:
    """Final balance and worst peak-to-trough drawdown, in percent."""
    balance = peak = start_balance
    worst = 0.0
    for pnl in pnls_usd:
        balance += pnl
        peak = max(peak, balance)
        worst = max(worst, (peak - balance) / peak)
    return balance, 100.0 * worst
