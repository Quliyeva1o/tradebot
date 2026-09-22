"""Where the FvgWindow_NDX100_Demo stop rule in deploy/kill_rules.json comes from.

The rule was fixed before the bot's first live trade, the way OrbBreakoutwf_XAUUSD_Demo's was:
a bootstrap envelope of the backtest's own trades, one number per clause --
  * each stop is the p99 max drawdown over the first 40 and 80 trades;
  * the checkpoint is the p05 net R at trade 40.

The trades are the Demo launcher's exact configuration (its own flags, parsed by the runner's
own parser) replayed by scripts/first_fvg_window_backtest.py on CFI's US100_Spot M1 history, with
CFI's spread charged as that script does. Swap is added here, because 45% of these trades are
held overnight and the backtest charges none: today's CFI rate from the spec snapshot, on every
broker-midnight the trade crosses, three on the broker's triple day
(backtest/live_replay/pricing.py, the replay's own rule). Today's rate on every year overstates
the low-rate years, so the swap-charged envelope is the stricter of the two bounds; the rule
uses it, and the swap-free figures are printed beside it.

The full 2019-2026 history is the basis, not the last year: the last year is this rule's best
(PF 1.27 after swap) and the last six months are negative, so an envelope drawn from the best
year would stop the bot for behaving like its own history.

Usage (needs data/history/cfi, which the VPS keeps):
    python -m scripts.fvg_window_envelope
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.resolve()))

from backtest.live_replay.brokers import BROKERS, history_path  # noqa: E402
from backtest.live_replay.pricing import rollover_days, swap_usd  # noqa: E402
from backtest.live_replay.specs import SymbolSpec, load_specs  # noqa: E402
from config.brokers import history_clock  # noqa: E402
from core.broker_clock import BrokerClock  # noqa: E402
from core.models import Bar, SignalDirection  # noqa: E402
from scripts.first_fvg_window_backtest import FrameBars, FvgTrade, run_backtest  # noqa: E402

REPO = Path(__file__).parent.parent
LAUNCHER = REPO / "run_live_fvg_window_ndx100_demo.bat"
SEED = 20260922
DRAWS = 100_000
STOP_WINDOWS = (40, 80)
CHECKPOINT = 40


def swap_r(trade: FvgTrade, spec: SymbolSpec, tp_r: float, clock: BrokerClock) -> float:
    """The swap one trade carries, in the R its lot size was sized on.

    The bot sizes on the plan's entry-to-stop distance. A fill through the limit enters at a better
    price, so that distance is recovered from the stop and target, which stay the plan's. Swap is
    charged at each server midnight on `clock`, the broker's server clock.
    """
    plan_risk = abs(trade.target - trade.stop) / (1 + tp_r)
    days = rollover_days(trade.entry_time.astimezone(clock).date(),
                         trade.exit_time.astimezone(clock).date(), spec.swap_rollover3days)
    direction = SignalDirection.BUY if trade.direction == "LONG" else SignalDirection.SELL
    usd = swap_usd(spec, direction, 1.0, trade.entry, days, usd_per_unit=1.0)
    return usd / (plan_risk * spec.usd_per_price_unit(1.0, 1.0))


def backtest_trades(bat: Path = LAUNCHER, broker_name: str = "cfi", history: Path | None = None) -> pd.DataFrame:
    """The launcher's configuration backtested on one broker's history: one row per trade.

    Columns: day, direction, r_net (spread charged), swap_r, r_swap (both). Empty when the machine
    has no history for that broker.
    """
    import run_live_first_fvg_window as runner
    from scripts.backtest_common import load_m1, resample
    from scripts.two_strategy_symbol_sweep import recent_spread

    args = runner.launcher_args(bat)
    cfg = runner.config_from(args)
    broker = BROKERS[broker_name]
    spec = load_specs(REPO / broker.specs_file)[args.symbol]
    path = history or REPO / history_path(broker, spec)
    if not path.exists():
        return pd.DataFrame(columns=["day", "direction", "r_net", "swap_r", "r_swap"])
    m1 = load_m1(str(path))
    signal = resample(m1, cfg.bar_minutes)
    signal_bars = [Bar(timestamp=ts.to_pydatetime().astimezone(UTC), open=r.open, high=r.high, low=r.low,
                       close=r.close, volume=0.0) for ts, r in zip(signal.index, signal.itertuples())]
    trades = run_backtest(signal_bars, FrameBars(m1), cfg, spec.broker_symbol or args.symbol, recent_spread(path))
    frame = pd.DataFrame({"day": [t.day for t in trades], "direction": [t.direction for t in trades],
                          "r_net": [t.r_net for t in trades],
                          "swap_r": [swap_r(t, spec, cfg.tp_r, history_clock(path)) for t in trades]})
    frame["r_swap"] = frame["r_net"] + frame["swap_r"]
    return frame


@dataclass(frozen=True)
class Envelope:
    max_dd: dict[int, float]  # p99 max drawdown within the first N trades
    net_at_checkpoint: float  # p05 net R at the checkpoint


def envelope(rs: np.ndarray, *, seed: int = SEED, draws: int = DRAWS) -> Envelope:
    """Bootstrap: `draws` sequences of trades drawn with replacement from `rs`.

    Drawdown is measured as scripts/kill_rule.max_drawdown measures the live trades: below the
    running peak of cumulative R, the flat start counting as a peak.
    """
    horizon = max(STOP_WINDOWS + (CHECKPOINT,))
    sample = np.random.default_rng(seed).choice(rs, size=(draws, horizon), replace=True)
    cumulative = np.concatenate([np.zeros((draws, 1)), sample.cumsum(axis=1)], axis=1)
    below_peak = np.maximum.accumulate(cumulative, axis=1) - cumulative
    return Envelope(
        max_dd={n: float(np.percentile(below_peak[:, :n + 1].max(axis=1), 99)) for n in STOP_WINDOWS},
        net_at_checkpoint=float(np.percentile(cumulative[:, CHECKPOINT], 5)),
    )


def _describe(label: str, rs: np.ndarray) -> None:
    losses = -rs[rs < 0].sum()
    env = envelope(rs)
    stops = "  ".join(f"p99 DD {n}: {env.max_dd[n]:.1f}R" for n in STOP_WINDOWS)
    print(f"{label:<28} n={len(rs):<4} PF={rs[rs > 0].sum() / losses:.3f} net={rs.sum():+7.1f}R   "
          f"{stops}   p05 net@{CHECKPOINT}: {env.net_at_checkpoint:+.1f}R")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--history", type=Path,
                    help="CFI M1 CSV; defaults to data/history/cfi's (a worktree has no data/ of its own)")
    args = ap.parse_args()

    frame = backtest_trades(history=args.history)
    if frame.empty:
        raise SystemExit("No CFI history on this machine -- pass --history")
    last_year = frame["day"] > frame["day"].max() - pd.Timedelta(days=365)
    print(f"{LAUNCHER.name} on CFI: {frame['day'].min()} -> {frame['day'].max()}; swap {frame['swap_r'].sum():+.1f}R "
          f"in total, {(frame['swap_r'] != 0).mean():.0%} of trades held overnight; "
          f"bootstrap {DRAWS} draws, seed {SEED}\n")
    _describe("FULL HISTORY, with swap  <-", frame["r_swap"].to_numpy())
    _describe("full history, no swap", frame["r_net"].to_numpy())
    _describe("last year, with swap", frame.loc[last_year, "r_swap"].to_numpy())


if __name__ == "__main__":
    main()
