"""The reverse trade --reverse-on-stop opens when a bot's trade stops out, priced like the broker.

The runner rests an opposite pending stop order at the trade's stop (execution/stop_and_reverse.py),
so it fills on the tick that stops the trade, at the same price, with the same lots, its stop back
at the original entry and its target R times that distance. This prices it from there:
  - where the broker has ticks, the rest of the stop's minute is walked on them, so the part of
    the bar BEFORE the stop cannot fake a hit; without ticks the stop bar is judged by the
    conservative entry-bar rule (a bar touching both levels is a stop);
  - after that, bar by bar, with the same exit rules and post-break gap pricing as any position;
  - swap at every server midnight crossed, commission once.

It is priced in full the moment the original stops, and the engine keeps the symbol's one position
busy until it closes -- the live bot sees the open reverse trade as its own and takes no new signal.
Checked 2026-09-17 on the six Demo bots: a scratch engine copy with this logic reproduced the repo
engine trade-for-trade with the rule off, and the replay matched the account's real 09-15/16 stops.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np

from backtest.live_replay.market import BarFrame, FxSeries
from backtest.live_replay.pricing import exit_on_bar, rollover_days, swap_usd
from backtest.live_replay.specs import SymbolSpec
from backtest.live_replay.ticks import TickCache
from core.models import SignalDirection

REVERSE_SUFFIX = "_sar"
_SEARCH_CHUNK = 20_000


def _first_touch(m1: BarFrame, start: int, long: bool, stop: float, target: float,
                 spread: np.ndarray) -> int | None:
    """Index of the first bar from `start` whose range reaches the stop or the target."""
    k0 = start
    while k0 < len(m1):
        k1 = min(len(m1), k0 + _SEARCH_CHUNK)
        if long:
            hit = (m1.low[k0:k1] <= stop) | (m1.high[k0:k1] >= target)
        else:
            sp = spread[k0:k1]
            hit = (m1.high[k0:k1] + sp >= stop) | (m1.low[k0:k1] + sp <= target)
        if hit.any():
            return k0 + int(np.argmax(hit))
        k0 = k1
    return None


def reverse_trade(original, reward_r: float, m1: BarFrame, spec: SymbolSpec, fx: FxSeries,
                  ticks: TickCache | None, flags, spread: np.ndarray, server_day: np.ndarray,
                  balance: float, *, gap_window_seconds: int, break_seconds: int, as_date):
    """(TradeRecord, exit bar index) of the reverse of `original`, a TradeRecord that stopped out.

    `spread` is each bar's spread as the engine charges it (scaled, or zeros with the flag off).
    """
    from backtest.live_replay.engine import TradeRecord  # engine imports this module

    ts = m1.ts
    j = int(np.searchsorted(ts, int(original.exit_time.timestamp())))
    long = original.direction == SignalDirection.SELL.name
    direction = SignalDirection.BUY if long else SignalDirection.SELL
    distance = abs(original.entry - original.stop)
    fill = original.exit
    stop = fill - distance if long else fill + distance
    target = fill + reward_r * distance if long else fill - reward_r * distance
    volume = original.volume
    risk_usd = distance * spec.usd_per_price_unit(volume, fx.usd_per_unit(int(ts[j])))
    commission = -spec.commission_per_lot_usd * volume if flags.commission else 0.0

    entry_index, start, entry_bar_rule = j, j, True
    exit_price: float | None = None
    reason = ""
    k = j
    if original.exit_reason in ("SL_GAP_PROXY", "SL_GAP_LEVEL"):
        start, entry_bar_rule = j + 1, False  # filled at the bar's close or level; the rest is gone
    elif ticks is not None and flags.entry_ticks and ticks.has_history(original.symbol, int(ts[j])):
        seconds = gap_window_seconds if original.exit_reason == "SL_GAP_TICK" else 60
        rows = ticks.window(original.symbol, int(ts[j]), seconds)
        cross = next((i for i, (_, bid, ask) in enumerate(rows)
                      if (bid <= original.stop if original.direction == "BUY" else ask >= original.stop)),
                     None)
        if cross is not None:
            stop_ms = rows[cross][0]
            entry_index = int(np.searchsorted(ts, (stop_ms // 60_000) * 60, side="right")) - 1
            minute_end = (stop_ms // 60_000 + 1) * 60_000
            for time_msc, bid, ask in rows[cross + 1:]:
                if time_msc >= minute_end:
                    break
                price = bid if long or not flags.spread else ask
                if (price <= stop) if long else (price >= stop):
                    exit_price, reason = stop, "SL"
                    break
                if (price >= target) if long else (price <= target):
                    exit_price, reason = target, "TP"
                    break
            k = entry_index
            start, entry_bar_rule = entry_index + 1, False

    touched_both = False
    if exit_price is None:
        found = _first_touch(m1, start, long, stop, target, spread)
        if found is None:
            k = len(m1) - 1
            exit_price, reason = float(m1.close[k]), "OPEN"
        else:
            k = found
            bar = m1.bar(k)
            after_break = k > 0 and int(ts[k]) - int(ts[k - 1]) > break_seconds
            hit = exit_on_bar(direction, stop, target, bar, float(spread[k]),
                              entry_bar=k == entry_index and entry_bar_rule, after_break=after_break)
            if hit is None:  # the vector test and exit_fill disagree only on float noise at a level
                stop_touched = bar.low <= stop if long else bar.high + spread[k] >= stop
                hit = (stop, "SL") if stop_touched else (target, "TP")
            exit_price, reason = hit
            if long:
                touched_both = bar.low <= stop and bar.high >= target
            else:
                touched_both = bar.high + spread[k] >= stop and bar.low + spread[k] <= target
            if reason == "SL" and after_break:
                reason = "SL_GAP_PROXY"
                tick_price = (ticks.first_crossing(original.symbol, int(ts[k]), direction, stop,
                                                   gap_window_seconds)
                              if flags.gap_ticks and ticks is not None else None)
                if tick_price is not None:
                    exit_price, reason = tick_price, "SL_GAP_TICK"
                elif not flags.gap_proxy:
                    exit_price, reason = stop, "SL_GAP_LEVEL"

    swap = 0.0
    if flags.swap and k > entry_index:
        # the engine charges a midnight on the first bar after it, never on the entry bar itself
        crossed = entry_index + 1 + np.flatnonzero(server_day[entry_index + 1:k + 1] > server_day[entry_index:k])
        for i in crossed:
            days = rollover_days(as_date(server_day[i - 1]), as_date(server_day[i]), spec.swap_rollover3days)
            swap += swap_usd(spec, direction, volume, float(m1.close[i - 1]), days,
                             fx.usd_per_unit(int(ts[i])))
    sign = 1.0 if long else -1.0
    pnl = ((exit_price - fill) * sign * spec.usd_per_price_unit(volume, fx.usd_per_unit(int(ts[k])))
           + swap + commission)
    record = TradeRecord(
        task=original.task, symbol=original.symbol, setup_id=f"{original.setup_id}{REVERSE_SUFFIX}",
        direction=direction.name, signal_time=original.exit_time,
        entry_time=datetime.fromtimestamp(int(ts[entry_index]), UTC),
        exit_time=datetime.fromtimestamp(int(ts[k]), UTC), entry=fill, stop=stop, target=target,
        exit=exit_price, exit_reason=reason, volume=volume, pnl_usd=pnl, swap_usd=swap,
        commission_usd=commission, risk_usd=risk_usd, r=pnl / risk_usd if risk_usd > 0 else 0.0,
        balance_after=balance + pnl, closed_on_entry_bar=k == entry_index,
        both_levels_touched=touched_both)
    return record, k
