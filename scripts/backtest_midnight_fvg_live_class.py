"""Full trade-outcome backtest of the LIVE MidnightFvgStrategy class
(strategy/midnight_fvg.py), on fresh NAS100_M1.csv.

Unlike scripts/replay_live_strategy_check_midnight_fvg.py (which only lists
setups, no outcome simulation) or scripts/first_fvg_backtest*.py (standalone
reimplementations that have drifted from the live class -- e.g. they predate
the wide-SL change), this script drives the ACTUAL live class bar-by-bar and
then simulates each setup forward through subsequent M1 bars to determine
whether SL or TP was hit first, so the resulting stats reflect exactly what
the currently-deployed live bot would have done historically.

One-trade-at-a-time gating: mirrors run_live_midnight_fvg.py's run_once(),
which never evaluates for a new trade while a position is open (regardless
of how many days pass) -- any setup proposed while a previous trade is still
unresolved is skipped entirely (not counted as a trade).

Same-bar SL+TP: SL takes precedence (matches TradeManager's convention
elsewhere in this repo).

Usage:
    python -m scripts.backtest_midnight_fvg_live_class
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core.models import Bar, SignalDirection, Timeframe
from market_structure.structure_models import MarketState
from strategy.midnight_fvg import MidnightFvgConfig, MidnightFvgStrategy

BROKER_TZ = ZoneInfo("Europe/Bucharest")
UTC = ZoneInfo("UTC")

INPUT_CSV = "data/history/NAS100_M1.csv"
OUTPUT_CSV = "artifacts/midnight_fvg_live_trades.csv"


def load_bars(path: str) -> list[Bar]:
    bars = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            naive = datetime.strptime(row["time"], "%Y-%m-%d %H:%M:%S")
            broker_local = naive.replace(tzinfo=BROKER_TZ)
            true_utc = broker_local.astimezone(UTC)
            bars.append(Bar(
                timestamp=true_utc, open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]), volume=0.0,
            ))
    bars.sort(key=lambda b: b.timestamp)
    return bars


def simulate_outcome(setup, bars: list[Bar], start_idx: int) -> tuple[float, datetime | None, bool]:
    """Scans bars[start_idx:] for the first SL or TP touch.

    Returns (r_multiple, exit_timestamp, resolved). resolved=False means
    neither level was hit before the data ran out (trade still "open" at
    the end of history -- excluded from stats, not counted as a loss).
    """
    direction = setup.direction
    entry = setup.entry_zone[0]
    sl = setup.stop_zone[0]
    tp = setup.target_zone[0]
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    win_r = reward / risk if risk > 0 else 0.0

    for bar in bars[start_idx:]:
        if direction == SignalDirection.BUY:
            hit_sl = bar.low <= sl
            hit_tp = bar.high >= tp
        else:
            hit_sl = bar.high >= sl
            hit_tp = bar.low <= tp
        if hit_sl:  # SL-takes-precedence tie-break on a same-bar double-touch
            return -1.0, bar.timestamp, True
        if hit_tp:
            return win_r, bar.timestamp, True
    return 0.0, None, False


def simulate_entry_bar_then_outcome(setup, bars: list, entry_idx: int) -> tuple[float, object | None, bool]:
    """Like simulate_outcome, but ALSO checks the entry bar's own remaining
    range for an immediate SL/TP touch.

    Regression coverage for a real bug: for a "touch" entry, the fill price
    is the FVG's near edge, reached partway through that bar's range -- the
    REST of that same bar's excursion (its low, for a BUY) can still reach
    SL before the bar closes. Scanning only from the NEXT bar onward (the
    original version of this function) missed same-bar stop-outs entirely:
    a 2026-08-25 NAS100 trade whose entry bar's low (29100.1) already
    breached the SL (29102.3) was wrongly reported as a +2.5R win because
    bar[i+1] happened to reach TP -- the position would have been stopped
    out within the entry bar itself, before that next bar even started.
    """
    direction = setup.direction
    sl = setup.stop_zone[0]
    tp = setup.target_zone[0]
    entry_bar = bars[entry_idx]
    if direction == SignalDirection.BUY:
        entry_bar_hit_sl = entry_bar.low <= sl
        entry_bar_hit_tp = entry_bar.high >= tp
    else:
        entry_bar_hit_sl = entry_bar.high >= sl
        entry_bar_hit_tp = entry_bar.low <= tp
    if entry_bar_hit_sl:  # SL-takes-precedence on a same-bar double-touch
        return -1.0, entry_bar.timestamp, True
    if entry_bar_hit_tp:
        entry = setup.entry_zone[0]
        risk = abs(entry - sl)
        reward = abs(tp - entry)
        win_r = reward / risk if risk > 0 else 0.0
        return win_r, entry_bar.timestamp, True
    return simulate_outcome(setup, bars, entry_idx + 1)


def main() -> None:
    bars = load_bars(INPUT_CSV)
    print(f"Loaded {len(bars)} bars: {bars[0].timestamp} -> {bars[-1].timestamp}")

    market_state = MarketState(symbol="NAS100", timeframe=Timeframe.M1)
    strategy = MidnightFvgStrategy(config=MidnightFvgConfig(fixed_tp_r=2.5, min_gap_points=3.0))

    trades: list[dict] = []
    open_until_idx: int | None = None  # bar index at/after which no trade is open

    for i, bar in enumerate(bars):
        market_state.append_bar(bar)
        setup = strategy.evaluate(market_state)
        if setup is None:
            continue
        if open_until_idx is not None and i < open_until_idx:
            # A prior trade is still unresolved -- run_once() would never
            # have evaluated for a new trade at all in this situation.
            continue

        r, exit_ts, resolved = simulate_entry_bar_then_outcome(setup, bars, i)
        if not resolved:
            break  # ran out of history mid-trade; stop (nothing after this can resolve either)

        trades.append({
            "entry_time": setup.timestamp.isoformat(),
            "exit_time": exit_ts.isoformat(),
            "direction": setup.direction.name,
            "entry_price": setup.entry_zone[0],
            "stop": setup.stop_zone[0],
            "r_multiple": r,
            "win": r > 0,
        })
        # Find the bar index of exit_ts to gate the next trade.
        for j in range(i + 1, len(bars)):
            if bars[j].timestamp == exit_ts:
                open_until_idx = j + 1
                break

    print(f"Total simulated trades: {len(trades)}")
    wins = sum(1 for t in trades if t["win"])
    gross_win = sum(t["r_multiple"] for t in trades if t["r_multiple"] > 0)
    gross_loss = sum(-t["r_multiple"] for t in trades if t["r_multiple"] < 0)
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    print(f"Win rate: {wins / len(trades) * 100:.1f}% | PF: {pf:.2f} | Net R: {sum(t['r_multiple'] for t in trades):.1f}")

    Path("artifacts").mkdir(exist_ok=True)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["entry_time", "exit_time", "direction", "entry_price", "stop", "r_multiple", "win"])
        writer.writeheader()
        writer.writerows(trades)
    print(f"Wrote {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
