"""One bot, replayed: polls every two minutes, pays the spread, and lets the broker hold the stop."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from backtest.live_replay.configs import BotConfig
from backtest.live_replay.engine import Flags, run
from backtest.live_replay.market import NY, FxSeries, frame_from_bars
from backtest.live_replay.specs import SymbolSpec
from core.models import Bar

USD = FxSeries("USD", None, None)
SYN = SymbolSpec(symbol="SYN", point=0.01, tick_size=0.01, contract_size=1.0, volume_min=0.01,
                 volume_step=0.01, volume_max=1e6, profit_currency="USD", swap_mode=5,
                 swap_long=0.0, swap_short=0.0, swap_rollover3days=5, margin_rate=0.0001,
                 commission_per_lot_usd=0.0)
CONFIG = BotConfig(task="T", family="breakout", symbol="SYN", paper=False, risk_pct=0.005,
                   scan_minutes=1, or_minutes=15, tp_r=4.0, entry_window_end=None)


def _bar(ts: datetime, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(timestamp=ts.astimezone(NY), open=o, high=h, low=low, close=c, volume=1.0, spread=0.1)


def _session(day: datetime, tail: list[tuple[float, float, float, float]]) -> list[Bar]:
    """09:30-09:44 range 100-101, a breakout close of 101.8 on the 09:52 bar, then `tail`."""
    bars = [_bar(day + timedelta(minutes=i), 100.5, 101.0, 100.0, 100.5) for i in range(15)]
    bars += [_bar(day + timedelta(minutes=i), 100.5, 100.9, 100.2, 100.6) for i in range(15, 22)]
    bars.append(_bar(day + timedelta(minutes=22), 100.9, 102.0, 100.9, 101.8))
    bars += [_bar(day + timedelta(minutes=23 + i), *ohlc) for i, ohlc in enumerate(tail)]
    return bars


FLAT = [(101.5, 101.6, 101.2, 101.4)] * 40
DAY = datetime(2026, 9, 10, 9, 30, tzinfo=NY)  # a Thursday


def _run(bars, config=CONFIG, spec=SYN, **kwargs):
    return run(config, frame_from_bars("SYN", 1, bars), spec, USD, **kwargs)


def test_entry_waits_for_the_next_even_minute_poll_and_pays_the_ask() -> None:
    trade = _run(_session(DAY, FLAT))[0]
    # the 09:52 bar closes at 09:53; the 09:54 poll is the first that sees it
    assert trade.entry_time.astimezone(NY).strftime("%H:%M") == "09:54"
    assert trade.entry == pytest.approx(101.6)  # 101.5 open + 0.1 spread
    assert (trade.stop, trade.target) == (100.0, pytest.approx(101.8 + 4 * 1.8))


def test_a_stop_is_filled_at_its_level_and_the_setup_is_never_reopened() -> None:
    tail = list(FLAT)
    tail[2] = (101.5, 101.6, 99.5, 99.8)  # the 09:55 bar trades through the stop
    trades = _run(_session(DAY, tail))
    assert len(trades) == 1
    assert (trades[0].exit, trades[0].exit_reason) == (100.0, "SL")
    assert trades[0].r == pytest.approx((100.0 - 101.6) / (101.6 - 100.0), abs=0.01)


def test_a_stop_after_a_trading_break_fills_at_the_break_bars_close() -> None:
    bars = _session(DAY, FLAT)
    after_break = DAY.replace(hour=18)  # the session gap the daily break leaves
    bars.append(_bar(after_break, 101.5, 101.5, 99.0, 99.2))
    trade = _run(bars)[0]
    assert (trade.exit, trade.exit_reason) == (99.2, "SL_GAP_PROXY")


def test_real_ticks_override_the_gap_proxy() -> None:
    class _Ticks:
        def first_crossing(self, symbol, start, direction, level, seconds=300):
            return 99.6

    bars = _session(DAY, FLAT)
    bars.append(_bar(DAY.replace(hour=18), 101.5, 101.5, 99.0, 99.2))
    trade = _run(bars, ticks=_Ticks())[0]
    assert (trade.exit, trade.exit_reason) == (99.6, "SL_GAP_TICK")


def test_the_old_backtest_assumption_fills_a_gap_stop_at_its_level() -> None:
    bars = _session(DAY, FLAT)
    bars.append(_bar(DAY.replace(hour=18), 101.5, 101.5, 99.0, 99.2))
    trade = _run(bars, flags=Flags(gap_ticks=False, gap_proxy=False))[0]
    assert (trade.exit, trade.exit_reason) == (100.0, "SL_GAP_LEVEL")


def test_without_the_poll_clock_it_enters_on_the_next_bar_like_a_batch_backtest() -> None:
    trade = _run(_session(DAY, FLAT), flags=Flags(poll_clock=False))[0]
    assert trade.entry_time.astimezone(NY).strftime("%H:%M") == "09:53"


def test_with_the_spread_switched_off_it_fills_at_the_bid() -> None:
    trade = _run(_session(DAY, FLAT), flags=Flags(spread=False))[0]
    assert trade.entry == pytest.approx(101.5)


def test_a_position_held_over_the_weekend_is_charged_three_swap_days() -> None:
    friday = datetime(2026, 9, 11, 9, 30, tzinfo=NY)
    bars = _session(friday, FLAT)
    monday = datetime(2026, 9, 14, 9, 30, tzinfo=NY)
    bars += [_bar(monday + timedelta(minutes=i), 101.5, 101.6, 101.2, 101.4) for i in range(3)]
    bars.append(_bar(monday + timedelta(minutes=3), 101.5, 110.0, 101.4, 109.5))  # hits the target
    spec = replace(SYN, swap_long=-7.33)
    trade = _run(bars, spec=spec)[0]
    expected = trade.volume * 1.0 * 101.4 * -7.33 / 100 / 360 * 3
    assert trade.exit_reason == "TP"
    assert trade.swap_usd == pytest.approx(expected, rel=0.01)


def test_a_trade_still_open_at_the_end_is_reported_but_marked_open() -> None:
    trades = _run(_session(DAY, FLAT))
    assert trades[-1].exit_reason == "OPEN"
