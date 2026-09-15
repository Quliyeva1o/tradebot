"""The replay must act on exactly the setup the live runner would, at every poll.

Drives the runners' own _evaluate_for_new_trade over the bars the VPS would have fetched at
each poll (run_once: DEFAULT_LOOKBACK_DAYS x bars a day) and compares with the replay's
signals. Needs data/history/fundingpips; skipped where it is absent.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import run_live_nasdaq_orb
import run_live_xauusd_orb
from backtest.live_replay.configs import scope
from backtest.live_replay.market import DEFAULT_DATA_DIR, NY, aggregate, load_bars, load_m1
from backtest.live_replay.signals import SWEEP_LOOKBACK_BARS, grace_bars, make_signals
from core.models import Timeframe
from execution.order import OrderStatus
from strategy.nasdaq_orb_m1_breakout import NasdaqOrbM1BreakoutConfig, NasdaqOrbM1BreakoutStrategy
from strategy.xauusd_orb_liquidity_sweep import (
    XauusdOrbLiquiditySweepConfig, XauusdOrbLiquiditySweepStrategy,
)

CONFIGS = scope()
DATA_PRESENT = all((DEFAULT_DATA_DIR / f"{c.symbol}_M1.csv").exists() for c in CONFIGS)
REPLAY_FROM = datetime(2025, 9, 1, tzinfo=UTC)
BREAKOUT_LOOKBACK = {1: 2 * 1440, 5: 2 * 288}  # run_live_nasdaq_orb.run_once
SETUP_DAYS, RANDOM_DAYS = 3, 3


class _Capture:
    """Stands in for TradeManager: fills everything and remembers the setup it was given."""

    def __init__(self) -> None:
        self.setups: list = []
        self.last_open_result = None
        self._position_sizer = None

    def open_trade(self, setup, broker):
        self.setups.append(setup)
        return SimpleNamespace(status=OrderStatus.FILLED, fill_price=setup.entry_zone[0],
                               order_id="replay")


@pytest.fixture(autouse=True)
def _quiet_runners(monkeypatch: pytest.MonkeyPatch) -> None:
    for runner in (run_live_nasdaq_orb, run_live_xauusd_orb):
        monkeypatch.setattr(runner, "_log_trade_event", lambda *_a, **_k: None)
        monkeypatch.setattr(runner, "_log_sizing", lambda *_a, **_k: None)


def _fresh_strategy(config):
    if config.family == "breakout":
        return NasdaqOrbM1BreakoutStrategy(
            config=NasdaqOrbM1BreakoutConfig(or_minutes=config.or_minutes, tp_r=config.tp_r,
                                             direction="long"))
    return XauusdOrbLiquiditySweepStrategy(config=XauusdOrbLiquiditySweepConfig())


def _runner_setup(config, bars, tmp_path):
    runner = run_live_nasdaq_orb if config.family == "breakout" else run_live_xauusd_orb
    capture = _Capture()
    ledger = tmp_path / "ledger.json"
    ledger.unlink(missing_ok=True)
    if bars:
        runner._evaluate_for_new_trade(
            capture, None, _fresh_strategy(config), bars, config.symbol,
            Timeframe(f"M{config.scan_minutes}"), grace_bars(config.scan_minutes),
            kill_switch_flag_path=tmp_path / "no_kill_switch.flag", traded_setups_path=ledger)
    return capture.setups[-1] if capture.setups else None


def _key(setup):
    return None if setup is None else (setup.setup_id, setup.entry_zone, setup.stop_zone,
                                       setup.target_zone)


def _polls(day):
    poll = datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY)
    while poll.hour < 12 or (poll.hour == 12 and poll.minute <= 30):
        yield poll
        poll += timedelta(minutes=2)


@pytest.mark.skipif(not DATA_PRESENT, reason="needs data/history/fundingpips")
@pytest.mark.parametrize("config", CONFIGS, ids=lambda c: c.task)
def test_the_replay_acts_on_the_same_setup_as_the_runner(config, tmp_path) -> None:
    m1 = load_m1(config.symbol, DEFAULT_DATA_DIR, start=REPLAY_FROM)
    scan = aggregate(m1, config.scan_minutes)
    lookback = (BREAKOUT_LOOKBACK[config.scan_minutes] if config.family == "breakout"
                else SWEEP_LOOKBACK_BARS)

    # Days the replay itself finds a setup on, so the comparison is not all None, plus random days.
    scout = make_signals(config, scan)
    setup_days, all_days = [], []
    for i in range(lookback, len(scan)):
        day = datetime.fromtimestamp(int(scan.ts[i]), UTC).astimezone(NY).date()
        if not all_days or all_days[-1] != day:
            all_days.append(day)
        if scout.at(i + 1) is not None and (not setup_days or setup_days[-1] != day):
            setup_days.append(day)
    assert setup_days, f"{config.task}: the replay found no setup at all in the sample window"
    rng = random.Random(20260915)
    days = sorted(set(rng.sample(setup_days, min(SETUP_DAYS, len(setup_days)))
                      + rng.sample(all_days, RANDOM_DAYS)))

    signals = make_signals(config, scan)
    for day in days:
        for poll in _polls(day):
            n_closed = scan.count_closed_by(int(poll.timestamp()))
            bars = [scan.bar(i) for i in range(max(0, n_closed - lookback), n_closed)]
            expected = _runner_setup(config, bars, tmp_path)
            assert _key(signals.at(n_closed)) == _key(expected), (
                f"{config.task} disagrees at {poll:%Y-%m-%d %H:%M} NY")


@pytest.mark.skipif(not DATA_PRESENT, reason="needs data/history/fundingpips")
@pytest.mark.parametrize("symbol", ["NDX100", "GER40", "XAUUSD"])
def test_m15_built_from_m1_matches_the_brokers_own_m15(symbol) -> None:
    native_path = DEFAULT_DATA_DIR / f"{symbol}_M15.csv"
    if not native_path.exists():
        pytest.skip(f"no native {symbol}_M15.csv to compare against")
    start = datetime(2026, 6, 1, tzinfo=UTC)
    built = aggregate(load_m1(symbol, DEFAULT_DATA_DIR, start=start), 15)
    native = load_bars(native_path, symbol, 15, start=start)
    built_by_ts = {int(t): i for i, t in enumerate(built.ts)}
    native_by_ts = {int(t): i for i, t in enumerate(native.ts)}
    shared = built_by_ts.keys() & native_by_ts.keys()
    assert len(shared) > 100
    mismatches = 0
    for ts in shared:
        b, n = built_by_ts[ts], native_by_ts[ts]
        if (abs(built.open[b] - native.open[n]) > 1e-9 or abs(built.high[b] - native.high[n]) > 1e-9
                or abs(built.low[b] - native.low[n]) > 1e-9
                or abs(built.close[b] - native.close[n]) > 1e-9):
            mismatches += 1
    assert mismatches / len(shared) < 0.005, f"{symbol}: {mismatches} of {len(shared)} M15 bars differ"
