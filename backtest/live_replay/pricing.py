"""How the broker prices one bot's entry, exit, swap and lot size.

Exits reuse execution/level_fill.py -- the same rules PaperBroker(level_fills=True) applies --
so a replayed trade and a paper trade are priced identically. That module works on bid bars,
which is right for a long; a short's stop and target trigger on the ask, so the bar is shifted
by the spread before it is priced.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date, timedelta

from core.models import Bar, SignalDirection
from execution.level_fill import exit_fill
from execution.position_sizer import PositionSizer
from strategy.models import TradeSetup
from strategy.risk_reward import resolve_entry_price, resolve_stop_and_target

from backtest.live_replay.specs import SymbolSpec

MAX_MARGIN_PCT = 0.20  # execution/trade_manager.py's DEFAULT_MAX_MARGIN_PCT


def entry_price(direction: SignalDirection, bar: Bar, spread: float) -> float:
    """A market order filled at `bar`'s open: a buy pays the ask, a sell gets the bid."""
    return bar.open + spread if direction == SignalDirection.BUY else bar.open


def ask_bar(bar: Bar, spread: float) -> Bar:
    return replace(bar, open=bar.open + spread, high=bar.high + spread, low=bar.low + spread,
                   close=bar.close + spread)


def exit_on_bar(direction: SignalDirection, stop: float, target: float, bar: Bar, spread: float, *,
                entry_bar: bool, after_break: bool) -> tuple[float, str] | None:
    """(price, "SL"|"TP") if this bar closes the trade, priced on the side that triggers."""
    priced = bar if direction == SignalDirection.BUY else ask_bar(bar, spread)
    return exit_fill(direction, stop, target, priced, entry_bar=entry_bar, after_break=after_break)


def rollover_days(held_from: date, held_to: date, triple_weekday: int) -> int:
    """Swap days charged for the server midnights crossed between the two dates.

    One per weeknight, three on `triple_weekday` (MT5 numbering, 0 = Sunday), none over the
    weekend -- the triple night is what pays for it.
    """
    days, day = 0, held_from
    while day < held_to:
        mt5_weekday = (day.weekday() + 1) % 7
        if 1 <= mt5_weekday <= 5:
            days += 3 if mt5_weekday == triple_weekday else 1
        day += timedelta(days=1)
    return days


def swap_usd(spec: SymbolSpec, direction: SignalDirection, volume: float, price: float, days: int,
             usd_per_unit: float) -> float:
    """What the broker charges for holding `volume` lots over `days` rollovers."""
    rate = spec.swap_long if direction == SignalDirection.BUY else spec.swap_short
    if spec.swap_mode == 1:      # points per lot per day (gold here)
        per_day = volume * spec.contract_size * spec.point * rate
    elif spec.swap_mode == 4:
        # Money per lot per day, already in the account's DEPOSIT currency (CFI's indices
        # quote this way). Unlike every other mode here it needs no profit-currency
        # conversion, so it returns before the usd_per_unit multiplication below.
        return volume * rate * days
    elif spec.swap_mode == 5:    # annual percent of the current price (the indices here)
        per_day = volume * spec.contract_size * price * rate / 100.0 / 360.0
    else:
        raise ValueError(f"{spec.symbol}: swap_mode {spec.swap_mode} is not modelled")
    return per_day * days * usd_per_unit


def size_position(spec: SymbolSpec, setup: TradeSetup, balance: float, usd_per_unit: float,
                  risk_pct: float) -> float:
    """TradeManager.open_trade's sizing: PositionSizer on balance, then the 20% margin ceiling.

    A flat bot's equity is its balance, so the ceiling is measured against that.
    """
    entry = resolve_entry_price(setup)
    stop, _ = resolve_stop_and_target(setup)
    volume = PositionSizer(risk_per_trade_pct=risk_pct).calculate_size(
        balance, entry, stop, spec.constraints(usd_per_unit))
    if volume <= 0:
        return 0.0
    margin = spec.margin_usd(volume, entry, usd_per_unit)
    ceiling = balance * MAX_MARGIN_PCT
    if margin <= ceiling:
        return volume
    capped = round(math.floor(volume * (ceiling / margin) / spec.volume_step) * spec.volume_step, 8)
    return capped if capped >= spec.volume_min else 0.0
