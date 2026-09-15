"""Tests for scripts/first_fvg_window_backtest.py -- one bot's trades, priced like the paper broker.

Every day uses the same M15 setup (NY time): candle 1 at 09:45 (high 100, low 90), the 10:00
candle, candle 3 at 10:15 (low 105). So: long limit 105, stop 90, target 150, working from
10:30 NY. In UTC (EDT) that is 14:30. Expected R values are worked out by hand.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from core.models import Bar
from scripts.first_fvg_window_backtest import run_backtest
from strategy.first_fvg_window import FirstFvgWindowConfig

NY = ZoneInfo("America/New_York")


def _ny(day: int, hh: int, mm: int, o: float, h: float, l: float, c: float) -> Bar:
    ts = datetime(2026, 9, day, hh, mm, tzinfo=NY).astimezone(UTC)
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=1.0)


def _setup_day(day: int) -> list[Bar]:
    return [_ny(day, 9, 45, 92, 100, 90, 98), _ny(day, 10, 0, 98, 121, 97, 120),
            _ny(day, 10, 15, 119, 125, 105, 124)]


CFG = FirstFvgWindowConfig()


def test_fills_at_the_limit_and_books_target_minus_spread_in_r() -> None:
    m1 = [_ny(14, 10, 30, 107, 108, 104, 106),   # touches 105 -> fills at 105
          _ny(14, 11, 0, 140, 150, 139, 149)]    # target 150

    trades = run_backtest(_setup_day(14), m1, CFG, symbol="NDX100", spread=1.5)

    assert len(trades) == 1
    t = trades[0]
    assert (t.day, t.direction, t.entry, t.stop, t.target) == (date(2026, 9, 14), "LONG", 105.0, 90.0, 150.0)
    assert (t.exit_price, t.reason) == (150.0, "TP")
    assert t.r_gross == 3.0
    assert round(t.r_net, 6) == 2.9                # 3 - 1.5 / 15
    assert t.setup_id == "setup_fvg_window_NDX100_20260914_BUY"


def test_touch_before_candle_three_closes_does_not_fill() -> None:
    m1 = [_ny(14, 10, 29, 107, 108, 100, 106),   # before 10:30: too early
          _ny(14, 10, 31, 107, 110, 106, 109)]   # never back to 105

    assert run_backtest(_setup_day(14), m1, CFG, symbol="NDX100", spread=1.5) == []


def test_unfilled_order_expires_at_new_york_midnight() -> None:
    m1 = [_ny(14, 10, 31, 107, 110, 106, 109),
          _ny(15, 0, 0, 104, 106, 100, 101)]     # touches 105 on the next NY date

    assert run_backtest(_setup_day(14), m1, CFG, symbol="NDX100", spread=1.5) == []


def test_next_setup_is_skipped_while_the_previous_trade_is_still_open() -> None:
    signal = _setup_day(14) + _setup_day(15)
    m1 = [_ny(14, 10, 30, 107, 108, 104, 106),   # day 1 fills at 105
          _ny(15, 10, 30, 107, 108, 104, 106),   # day 2 would fill here, but day 1 is still open
          _ny(15, 11, 0, 140, 150, 139, 149)]    # day 1 exits at target

    trades = run_backtest(signal, m1, CFG, symbol="NDX100", spread=1.5)

    assert [t.day for t in trades] == [date(2026, 9, 14)]
