"""A poll acts on the newest setup of today, and only while it is inside the grace window."""

from datetime import datetime, timedelta

import pytest

from backtest.live_replay.configs import BotConfig
from backtest.live_replay.market import NY, frame_from_bars
from backtest.live_replay.signals import grace_bars, make_signals
from core.models import Bar

BREAKOUT = BotConfig(task="T", family="breakout", symbol="SYN", paper=False, risk_pct=0.005,
                     scan_minutes=1, or_minutes=15, tp_r=4.0, entry_window_end=None)


def _bar(ts: datetime, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(timestamp=ts.astimezone(NY), open=o, high=h, low=low, close=c, volume=1.0, spread=0.1)


def _orb_day(day: datetime, tail: int = 20) -> list[Bar]:
    """A 09:30-09:44 range of 100-101, a breakout close at 09:52, then a quiet tail."""
    bars = [_bar(day + timedelta(minutes=i), 100.5, 101.0, 100.0, 100.5) for i in range(15)]
    bars += [_bar(day + timedelta(minutes=i), 100.5, 100.9, 100.2, 100.6) for i in range(15, 22)]
    bars.append(_bar(day + timedelta(minutes=22), 100.9, 102.0, 100.9, 101.8))
    bars += [_bar(day + timedelta(minutes=23 + i), 101.5, 101.6, 101.2, 101.4) for i in range(tail)]
    return bars


def test_grace_is_four_minutes_expressed_in_bars() -> None:
    assert (grace_bars(1), grace_bars(5), grace_bars(15)) == (4, 1, 1)


def test_the_breakout_setup_is_actionable_for_four_bars_then_expires() -> None:
    day = datetime(2026, 9, 10, 9, 30, tzinfo=NY)
    signals = make_signals(BREAKOUT, frame_from_bars("SYN", 1, _orb_day(day)))
    breakout_closed = 23  # bars 0..22 have closed once bar 22 is done
    assert signals.at(breakout_closed) is not None
    assert signals.at(breakout_closed + 4) is not None
    assert signals.at(breakout_closed + 5) is None


def test_the_setup_carries_the_opening_ranges_low_as_its_stop_and_a_four_r_target() -> None:
    day = datetime(2026, 9, 10, 9, 30, tzinfo=NY)
    setup = make_signals(BREAKOUT, frame_from_bars("SYN", 1, _orb_day(day))).at(23)
    assert setup.stop_zone == (100.0, 100.0)
    assert setup.entry_zone[0] == pytest.approx(101.8)
    assert setup.target_zone[0] == pytest.approx(101.8 + 4 * 1.8)


def test_yesterdays_setup_is_never_acted_on_today() -> None:
    first = _orb_day(datetime(2026, 9, 10, 9, 30, tzinfo=NY), tail=30)
    second = [_bar(datetime(2026, 9, 11, 9, 30, tzinfo=NY) + timedelta(minutes=i), 100.5, 100.9, 100.2, 100.6)
              for i in range(10)]
    signals = make_signals(BREAKOUT, frame_from_bars("SYN", 1, first + second))
    assert signals.at(len(first) + 10) is None


def test_the_sweep_is_not_replayed_outside_its_entry_window() -> None:
    config = BotConfig(task="S", family="sweep", symbol="SYN", paper=False, risk_pct=0.005,
                       scan_minutes=15, or_minutes=None, tp_r=None, entry_window_end=None)
    morning = datetime(2026, 9, 10, 8, 0, tzinfo=NY)
    bars = [_bar(morning + timedelta(minutes=15 * i), 100.0, 101.0, 99.0, 100.0) for i in range(4)]
    assert make_signals(config, frame_from_bars("SYN", 15, bars)).at(4) is None
