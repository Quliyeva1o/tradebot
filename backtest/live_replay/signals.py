"""Which setup a runner would act on at a given poll.

Mirrors run_live_nasdaq_orb.py / run_live_xauusd_orb.py's _evaluate_for_new_trade: replay the
fetched bars through the strategy, keep the newest setup, and act on it only while it belongs
to today and is no more than SIGNAL_GRACE_MINUTES old.

The two families need different handling. The breakout class resets its state at every NY date
change and reads only the newest bar, so feeding bars once, in order, is identical to the
runner's per-poll replay of two days (tests/live_replay/test_runner_equivalence.py proves it).
The sweep class seeds a Wilder ATR(14) from whatever window it is fed, and the runner feeds it
three days -- so it must be rebuilt on that same window whenever a bar closes.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, time

from core.models import Timeframe
from market_structure.structure_models import MarketState
from strategy.models import TradeSetup
from strategy.nasdaq_orb_m1_breakout import NasdaqOrbM1BreakoutConfig, NasdaqOrbM1BreakoutStrategy
from strategy.xauusd_orb_liquidity_sweep import (
    XauusdOrbLiquiditySweepConfig, XauusdOrbLiquiditySweepStrategy,
)

from backtest.live_replay.configs import BotConfig
from backtest.live_replay.market import NY, BarFrame

SIGNAL_GRACE_MINUTES = 4          # both runners
SWEEP_LOOKBACK_BARS = 3 * 96      # run_live_xauusd_orb.py: DEFAULT_LOOKBACK_DAYS x M15 bars a day
_STATE_RESET_BARS = 1024          # the classes read only the newest bar; keep MarketState small


def grace_bars(scan_minutes: int) -> int:
    return max(1, SIGNAL_GRACE_MINUTES // scan_minutes)


def _ny_date(frame: BarFrame, index: int):
    return datetime.fromtimestamp(int(frame.ts[index]), UTC).astimezone(NY).date()


def _actionable(found: tuple[TradeSetup, int] | None, n_closed: int, frame: BarFrame,
                grace: int) -> TradeSetup | None:
    if found is None or n_closed == 0:
        return None
    setup, index = found
    if setup.timestamp.astimezone(NY).date() != _ny_date(frame, n_closed - 1):
        return None
    return setup if n_closed - 1 - index <= grace else None


class BreakoutSignals:
    """One strategy instance, fed every closed scan bar exactly once."""

    def __init__(self, config: BotConfig, scan: BarFrame) -> None:
        self._scan = scan
        self._symbol = config.symbol
        self._timeframe = Timeframe(f"M{config.scan_minutes}")
        self._strategy = NasdaqOrbM1BreakoutStrategy(
            config=NasdaqOrbM1BreakoutConfig(or_minutes=config.or_minutes, tp_r=config.tp_r,
                                             direction="long"))
        self._state = MarketState(symbol=self._symbol, timeframe=self._timeframe)
        self._fed = 0
        self._found: tuple[TradeSetup, int] | None = None
        self._grace = grace_bars(config.scan_minutes)

    def at(self, n_closed: int) -> TradeSetup | None:
        while self._fed < n_closed:
            if self._state.bar_count() >= _STATE_RESET_BARS:
                self._state = MarketState(symbol=self._symbol, timeframe=self._timeframe)
            self._state.append_bar(self._scan.bar(self._fed))
            setup = self._strategy.evaluate(self._state)
            if setup is not None:
                self._found = (setup, self._fed)
            self._fed += 1
        return _actionable(self._found, n_closed, self._scan, self._grace)


class SweepSignals:
    """A fresh strategy over the runner's last three days of bars, rebuilt as bars close."""

    def __init__(self, config: BotConfig, scan: BarFrame,
                 lookback_bars: int = SWEEP_LOOKBACK_BARS) -> None:
        cfg = XauusdOrbLiquiditySweepConfig()
        if config.entry_window_end:
            hour, minute = (int(part) for part in config.entry_window_end.split(":"))
            cfg = replace(cfg, entry_window_end=time(hour, minute))
        self._cfg, self._scan, self._lookback = cfg, scan, lookback_bars
        self._symbol = config.symbol
        self._timeframe = Timeframe(f"M{config.scan_minutes}")
        self._grace = grace_bars(config.scan_minutes)
        self._last_n, self._last = -1, None
        # A setup bar opens after the opening-range candle and before the entry window ends; the
        # newest bar can be that bar or up to `grace` bars later. Outside that, nothing is
        # actionable, so the replay is skipped.
        self._first_minute = _minutes(cfg.or_start) + config.scan_minutes
        self._last_minute = _minutes(cfg.entry_window_end) + self._grace * config.scan_minutes

    def at(self, n_closed: int) -> TradeSetup | None:
        if n_closed != self._last_n:
            self._last_n = n_closed
            self._last = self._replay(n_closed) if self._in_window(n_closed) else None
        return self._last

    def _in_window(self, n_closed: int) -> bool:
        if n_closed == 0:
            return False
        local = datetime.fromtimestamp(int(self._scan.ts[n_closed - 1]), UTC).astimezone(NY)
        return self._first_minute <= local.hour * 60 + local.minute <= self._last_minute

    def _replay(self, n_closed: int) -> TradeSetup | None:
        strategy = XauusdOrbLiquiditySweepStrategy(config=self._cfg)
        state = MarketState(symbol=self._symbol, timeframe=self._timeframe)
        found: tuple[TradeSetup, int] | None = None
        for i in range(max(0, n_closed - self._lookback), n_closed):
            state.append_bar(self._scan.bar(i))
            setup = strategy.evaluate(state)
            if setup is not None:
                found = (setup, i)
        return _actionable(found, n_closed, self._scan, self._grace)


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


def make_signals(config: BotConfig, scan: BarFrame) -> BreakoutSignals | SweepSignals:
    return BreakoutSignals(config, scan) if config.family == "breakout" else SweepSignals(config, scan)
