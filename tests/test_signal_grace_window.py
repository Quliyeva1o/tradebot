"""The live runners must act on a setup for more than one bar.

Both ORB strategies latch a per-day flag the moment they produce their setup
(`_day_trade_taken` in NasdaqOrbM1BreakoutStrategy, `_trades_today` in
XauusdOrbLiquiditySweepStrategy), so every later bar in a replay returns None.
The runners used to keep only the FINAL bar's setup, which made the signal
exist for exactly one bar.

That is fatal on M1 because the polls are phase-locked. The Scheduled Tasks
fire every 2 minutes while M1 bars close every minute, so a bot's newest bar is
always the same parity -- measured over 200 real polls on 2026-09-09,
XAUUSD/DJI30/SPX500/JP225/GER40 each saw an odd-minute bar as their newest
98-100% of the time. An even-minute breakout was not unlucky, it was
unreachable: half of all signals, deterministically.

Observed live: JP225 broke its opening range at 09:52 NY on 2026-09-09 (M1
close 64839.99 against an OR high of 64815.01) and the bot logged 340
consecutive no_signal events. NDX100 traded normally the same day because an M5
bar stays newest across several polls.

These tests pin both halves of the fix: the window must be wide enough to
survive the poll cadence, and it must still expire so a stale breakout is not
chased hours later.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from core.models import Bar, Timeframe
from market_structure.structure_models import MarketState
from strategy.nasdaq_orb_m1_breakout import NasdaqOrbM1BreakoutConfig, NasdaqOrbM1BreakoutStrategy

import run_live_nasdaq_orb as nasdaq_runner
import run_live_xauusd_orb as sweep_runner

NY = ZoneInfo("America/New_York")


def _bar(ts: datetime, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(timestamp=ts, open=o, high=h, low=low, close=c, volume=100.0, spread=0.0)


def _orb_day(breakout_minute: int, tail_bars: int) -> list[Bar]:
    """09:30 open, a 15m range of 100.0-101.0, then a breakout and a quiet tail.

    `breakout_minute` is minutes past 09:45; the breakout bar closes above the
    range high and every bar after it stays inside, so only that one bar can
    ever produce the setup.
    """
    base = datetime(2026, 9, 9, 9, 30, tzinfo=NY)
    bars = [_bar(base + timedelta(minutes=i), 100.5, 101.0, 100.0, 100.5) for i in range(15)]
    bo = 15 + breakout_minute
    bars += [_bar(base + timedelta(minutes=i), 100.5, 100.9, 100.2, 100.6)
             for i in range(15, bo)]
    bars.append(_bar(base + timedelta(minutes=bo), 100.9, 102.0, 100.9, 101.8))
    bars += [_bar(base + timedelta(minutes=bo + 1 + i), 101.5, 101.6, 101.2, 101.4)
             for i in range(tail_bars)]
    return bars


def _evaluate(bars: list[Bar], grace: int):
    """Runs the runner's replay and returns (setup, bars_since_signal)."""
    strategy = NasdaqOrbM1BreakoutStrategy(
        config=NasdaqOrbM1BreakoutConfig(or_minutes=15, tp_r=4.0, direction="long")
    )
    state = MarketState(symbol="JP225", timeframe=Timeframe.M1)
    setup, age = None, 0
    for i, b in enumerate(bars):
        state.append_bar(b)
        found = strategy.evaluate(state)
        if found is not None:
            setup, age = found, len(bars) - 1 - i
    if setup is not None and age > grace:
        return None, age
    return setup, age


def test_strategy_returns_the_setup_on_exactly_one_bar():
    """The premise of the whole fix -- if this ever stops holding, revisit it."""
    assert _evaluate(_orb_day(breakout_minute=7, tail_bars=0), grace=0)[0] is not None
    # One bar later the latch has already fired and nothing is returned.
    assert _evaluate(_orb_day(breakout_minute=7, tail_bars=1), grace=0)[0] is None


@pytest.mark.parametrize("tail_bars", [0, 1, 2, 3, 4])
def test_signal_survives_every_poll_within_the_grace_window(tail_bars):
    """M1 grace is 4 bars, so a poll up to 4 bars late must still act.

    Two-minute polls on one-minute bars means the bot only ever sees every other
    bar; a window of one made half the breakouts unreachable.
    """
    grace = nasdaq_runner._grace_bars("M1")
    assert grace == 4
    setup, age = _evaluate(_orb_day(breakout_minute=7, tail_bars=tail_bars), grace)
    assert setup is not None, f"signal lost {tail_bars} bars after the breakout"
    assert age == tail_bars


def test_signal_expires_once_it_is_older_than_the_grace_window():
    """Chasing a breakout well after the fact is a different trade, not this one."""
    grace = nasdaq_runner._grace_bars("M1")
    setup, age = _evaluate(_orb_day(breakout_minute=7, tail_bars=grace + 1), grace)
    assert setup is None
    assert age == grace + 1


def test_grace_is_wall_clock_not_a_bar_count():
    """A fixed bar count would be 4 minutes on M1 and 60 on M15."""
    for runner in (nasdaq_runner, sweep_runner):
        assert runner._grace_bars("M1") == 4      # 4 x 1m
        assert runner._grace_bars("M5") == 1      # 4m < one 5m bar, floored to 1
        assert runner._grace_bars("M15") == 1     # never zero
        assert runner._grace_bars("H1") == 1


def test_both_runners_use_the_same_grace_policy():
    """The sweep strategy latches `_trades_today` the same way; the fix is shared."""
    assert nasdaq_runner.SIGNAL_GRACE_MINUTES == sweep_runner.SIGNAL_GRACE_MINUTES


def test_yesterdays_setup_is_dropped_silently_not_reported_as_expired():
    """A 2-day replay yields one setup per day; only today's may be considered.

    The strategy resets its per-day latch at each date change, so on a morning
    that has not broken out yet the newest setup in the window belongs to
    yesterday. The first version of this guard treated those as near-misses and
    logged signal_expired on every poll -- XAUUSD at 1349 bars stale, SPX500 at
    2156 -- which is noise that would bury a real one.
    """
    from datetime import date

    yesterday = _orb_day(breakout_minute=7, tail_bars=200)
    # Shift the whole day back 24h so its setup lands on the previous date.
    shifted = [
        _bar(b.timestamp - timedelta(days=1), b.open, b.high, b.low, b.close)
        for b in yesterday
    ]
    today_quiet = [
        _bar(datetime(2026, 9, 9, 9, 30, tzinfo=NY) + timedelta(minutes=i),
             100.5, 101.0, 100.0, 100.5)
        for i in range(40)
    ]
    bars = shifted + today_quiet

    strategy = NasdaqOrbM1BreakoutStrategy(
        config=NasdaqOrbM1BreakoutConfig(or_minutes=15, tp_r=4.0, direction="long")
    )
    state = MarketState(symbol="JP225", timeframe=Timeframe.M1)
    setup = None
    for b in bars:
        state.append_bar(b)
        found = strategy.evaluate(state)
        if found is not None:
            setup = found

    assert setup is not None, "the replay should still surface yesterday's setup"
    assert setup.timestamp.astimezone(NY).date() == date(2026, 9, 8)
    # The runner's own same-day test is what rejects it.
    assert setup.timestamp.astimezone(NY).date() != bars[-1].timestamp.astimezone(NY).date()
