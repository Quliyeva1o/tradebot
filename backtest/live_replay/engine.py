"""One bot, replayed the way the VPS runs it.

The loop walks M1 bars. While a position is open the broker holds its stop and target, so they
are checked on every bar and swap is charged at each server midnight. While flat, the bot polls
every two minutes (the Scheduled Task interval), sees only bars that have closed, and fills a
market order at the open of the bar its poll lands in.

Each realism feature is a flag, so the report can measure what it costs by switching it off.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from core.models import SignalDirection
from strategy.risk_reward import resolve_stop_and_target

from backtest.live_replay.configs import BotConfig
from backtest.live_replay.market import BROKER_TZ, BarFrame, FxSeries, aggregate
from backtest.live_replay.pricing import entry_price, exit_on_bar, rollover_days, size_position, swap_usd
from backtest.live_replay.signals import make_signals
from backtest.live_replay.specs import SymbolSpec
from backtest.live_replay.ticks import TickCache

POLL_SECONDS = 120            # the VPS Scheduled Task interval
TRADING_BREAK_SECONDS = 1800  # execution/paper_broker.py's _TRADING_BREAK
GAP_TICK_WINDOW_SECONDS = 300
# How far into its minute a poll's market order fills: the Scheduled Task has to start Python,
# fetch bars and send the order. The real Demo entries were stamped :04-:06 (16:46:04, 17:10:06,
# 18:46:05); MT5 truncates deal times to the second, so the true average is ~5.8 s. Against those
# ten fills, 6 s gives the smallest mean error (2.4 points; 5 s gives 2.7).
POLL_OFFSET_SECONDS = 6


@dataclass(frozen=True)
class Flags:
    """Realism features; each can be switched off to measure what it costs."""

    poll_clock: bool = True   # False: act on the bar after the signal, as the batch backtests do
    spread: bool = True       # False: fills and short exits at the bid
    entry_ticks: bool = True  # False: fill at the bar open even where real ticks exist
    gap_ticks: bool = True    # False: never price a post-break stop from real ticks
    gap_proxy: bool = True    # False: a post-break stop fills at its level (the batch assumption)
    swap: bool = True
    commission: bool = True


@dataclass(frozen=True)
class TradeRecord:
    task: str
    symbol: str
    setup_id: str
    direction: str
    signal_time: datetime
    entry_time: datetime
    exit_time: datetime
    entry: float
    stop: float
    target: float
    exit: float
    exit_reason: str  # TP | SL | SL_GAP_TICK | SL_GAP_PROXY | SL_GAP_LEVEL | OPEN
    volume: float
    pnl_usd: float
    swap_usd: float
    commission_usd: float
    risk_usd: float
    r: float
    balance_after: float
    closed_on_entry_bar: bool
    both_levels_touched: bool


@dataclass
class _Position:
    setup: object
    direction: SignalDirection
    entry_index: int
    fill: float
    stop: float
    target: float
    volume: float
    risk_usd: float
    commission_usd: float
    swap_usd: float = 0.0


def _server_days(ts: np.ndarray) -> np.ndarray:
    """yyyymmdd of each bar in broker server time, for spotting midnight rollovers."""
    local = pd.DatetimeIndex(pd.to_datetime(ts, unit="s", utc=True)).tz_convert(BROKER_TZ)
    return (local.year * 10000 + local.month * 100 + local.day).to_numpy(dtype=np.int64)


def _as_date(yyyymmdd: int):
    return datetime.strptime(str(int(yyyymmdd)), "%Y%m%d").date()


def run(config: BotConfig, m1: BarFrame, spec: SymbolSpec, fx: FxSeries, *,
        ticks: TickCache | None = None, flags: Flags = Flags(), start_balance: float = 50_000.0,
        spread_scale: float = 1.0) -> list[TradeRecord]:
    scan = aggregate(m1, config.scan_minutes)
    signals = make_signals(config, scan)
    server_day = _server_days(m1.ts)
    traded: set[str] = set()
    trades: list[TradeRecord] = []
    balance = start_balance
    position: _Position | None = None

    def spread_at(j: int) -> float:
        return float(m1.spread[j]) * spread_scale if flags.spread else 0.0

    def close(j: int, price: float, reason: str, *, touched_both: bool = False) -> None:
        nonlocal balance, position
        held = position
        usd = fx.usd_per_unit(int(m1.ts[j]))
        sign = 1.0 if held.direction == SignalDirection.BUY else -1.0
        pnl = ((price - held.fill) * sign * spec.usd_per_price_unit(held.volume, usd)
               + held.swap_usd + held.commission_usd)
        balance += pnl
        trades.append(TradeRecord(
            task=config.task, symbol=config.symbol, setup_id=held.setup.setup_id,
            direction=held.direction.name, signal_time=held.setup.timestamp,
            entry_time=datetime.fromtimestamp(int(m1.ts[held.entry_index]), UTC),
            exit_time=datetime.fromtimestamp(int(m1.ts[j]), UTC), entry=held.fill, stop=held.stop,
            target=held.target, exit=price, exit_reason=reason, volume=held.volume, pnl_usd=pnl,
            swap_usd=held.swap_usd, commission_usd=held.commission_usd, risk_usd=held.risk_usd,
            r=pnl / held.risk_usd if held.risk_usd > 0 else 0.0, balance_after=balance,
            closed_on_entry_bar=j == held.entry_index, both_levels_touched=touched_both))
        position = None

    def check_exit(j: int) -> bool:
        after_break = j > 0 and int(m1.ts[j]) - int(m1.ts[j - 1]) > TRADING_BREAK_SECONDS
        bar = m1.bar(j)
        hit = exit_on_bar(position.direction, position.stop, position.target, bar, spread_at(j),
                          entry_bar=j == position.entry_index, after_break=after_break)
        if hit is None:
            return False
        price, reason = hit
        if position.direction == SignalDirection.BUY:
            touched_both = bar.low <= position.stop and bar.high >= position.target
        else:
            ask_high, ask_low = bar.high + spread_at(j), bar.low + spread_at(j)
            touched_both = ask_high >= position.stop and ask_low <= position.target
        if reason == "SL" and after_break:
            reason = "SL_GAP_PROXY"
            tick_price = (ticks.first_crossing(config.symbol, int(m1.ts[j]), position.direction,
                                               position.stop, GAP_TICK_WINDOW_SECONDS)
                          if flags.gap_ticks and ticks is not None else None)
            if tick_price is not None:
                price, reason = tick_price, "SL_GAP_TICK"
            elif not flags.gap_proxy:
                price, reason = position.stop, "SL_GAP_LEVEL"
        close(j, price, reason, touched_both=touched_both)
        return True

    def poll_time(j: int) -> int | None:
        """The poll this bar serves: an even minute at its open, or the even minute just before
        it when that minute had no bar. A poll during a longer gap hits a closed market, which
        the runner simply retries at its next poll."""
        t = int(m1.ts[j])
        if (t // 60) % 2 == 0:
            return t
        previous = int(m1.ts[j - 1]) if j > 0 else None
        return t - 60 if previous is None or t - 60 > previous else None

    for j in range(len(m1)):
        if position is not None:
            if flags.swap and j > 0 and server_day[j] > server_day[j - 1]:
                days = rollover_days(_as_date(server_day[j - 1]), _as_date(server_day[j]),
                                     spec.swap_rollover3days)
                position.swap_usd += swap_usd(spec, position.direction, position.volume,
                                              float(m1.close[j - 1]), days,
                                              fx.usd_per_unit(int(m1.ts[j])))
            check_exit(j)  # a bar that closes a trade never opens the next one
            continue

        poll = int(m1.ts[j]) if not flags.poll_clock else poll_time(j)
        if poll is None:
            continue
        setup = signals.at(scan.count_closed_by(poll))
        if setup is None or setup.setup_id in traded:
            continue
        usd = fx.usd_per_unit(int(m1.ts[j]))
        volume = size_position(spec, setup, balance, usd, config.risk_pct)
        if volume <= 0:
            continue  # the live sizer would reject it locally and retry at the next poll
        stop, target = resolve_stop_and_target(setup)
        # A poll served by a later bar (its own minute had none) fills at that bar's first tick.
        offset = POLL_OFFSET_SECONDS if poll == int(m1.ts[j]) else 0
        quote = (ticks.entry_quote(config.symbol, int(m1.ts[j]), offset)
                 if flags.entry_ticks and ticks is not None else None)
        if quote is None:
            fill = entry_price(setup.direction, m1.bar(j), spread_at(j))
        else:
            bid, ask = quote
            fill = ask if setup.direction == SignalDirection.BUY and flags.spread else bid
        position = _Position(
            setup=setup, direction=setup.direction, entry_index=j, fill=fill, stop=stop,
            target=target, volume=volume,
            risk_usd=abs(fill - stop) * spec.usd_per_price_unit(volume, usd),
            commission_usd=-spec.commission_per_lot_usd * volume if flags.commission else 0.0)
        traded.add(setup.setup_id)
        check_exit(j)

    if position is not None:
        close(len(m1) - 1, float(m1.close[-1]), "OPEN")
    return trades
