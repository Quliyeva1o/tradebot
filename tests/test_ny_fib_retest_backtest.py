"""Tests for scripts/ny_fib_retest_backtest.py -- one bot's trades, priced like the paper broker.

Unless a test says otherwise the day is 2026-09-14 (NY, EDT) with a 100-110 range from
09:30-10:00 and a 10:00 close of 107. So: long limit 105, stop 102.36, target 116.18,
risk 2.64, working from 10:01 NY. Expected R values are worked out by hand.
"""

from datetime import date, time
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scripts.ny_fib_retest_backtest import run_backtest
from strategy.ny_fib_retest import NyFibRetestConfig

NY = ZoneInfo("America/New_York")
CFG = NyFibRetestConfig()
RISK = 2.64  # 105 - 102.36


def _frame(rows: list[tuple[int, int, int, float, float, float, float]]) -> pd.DataFrame:
    """(day, hh, mm, open, high, low, close) in NY wall clock -> a load_m1()-shaped frame."""
    index = pd.DatetimeIndex(
        [pd.Timestamp(2026, 9, d, hh, mm, tz=NY) for d, hh, mm, *_ in rows], name="ts"
    )
    return pd.DataFrame(
        {"open": [r[3] for r in rows], "high": [r[4] for r in rows],
         "low": [r[5] for r in rows], "close": [r[6] for r in rows],
         "volume": [1.0] * len(rows)},
        index=index,
    )


def _morning(day: int = 14) -> list[tuple[int, int, int, float, float, float, float]]:
    return [
        (day, 9, 30, 104, 106, 103, 105),
        (day, 9, 45, 105, 110, 104, 108),   # the range high
        (day, 9, 59, 108, 109, 100, 101),   # the range low
        (day, 10, 0, 101, 108, 104, 107),   # the bias bar: closes above 105, and dips to 104
    ]


def test_fills_at_the_midline_and_books_the_target_minus_spread() -> None:
    frame = _frame([*_morning(),
                    (14, 10, 1, 107, 108, 104, 106),    # touches 105 -> fills at 105
                    (14, 11, 0, 106, 117, 105, 116)])   # reaches 116.18

    trades, counts = run_backtest(frame, CFG, "NDX100", spread=0.264)

    assert len(trades) == 1
    t = trades[0]
    assert (t.day, t.direction) == (date(2026, 9, 14), "LONG")
    assert (t.entry, t.stop, t.target) == pytest.approx((105.0, 102.36, 116.18))
    assert t.reason == "TP"
    assert t.r_gross == pytest.approx(11.18 / RISK)
    assert t.r_net == pytest.approx(11.18 / RISK - 0.1)   # 0.264 of spread over 2.64 of risk
    assert t.bars_held == 1
    assert t.setup_id == "setup_ny_fib_NDX100_20260914_BUY"
    assert counts["no_fill"] == 0


def test_short_side_stops_out_above_the_entry() -> None:
    morning = [*_morning()[:3], (14, 10, 0, 101, 108, 100, 103)]  # closes below 105 -> short
    frame = _frame([*morning,
                    (14, 10, 1, 103, 106, 102, 104),    # touches 105 -> fills at 105
                    (14, 11, 0, 104, 108, 103, 107)])   # reaches 107.64

    trades, _ = run_backtest(frame, CFG, "NDX100", spread=0.0)

    assert len(trades) == 1
    t = trades[0]
    assert t.direction == "SHORT"
    assert (t.entry, t.stop, t.target) == pytest.approx((105.0, 107.64, 93.82))
    assert (t.reason, t.r_gross) == ("SL", pytest.approx(-1.0))


def test_the_bias_bars_own_touch_of_the_midline_does_not_fill() -> None:
    # The 10:00 bar dips to 104, but the bias it carries is only knowable at its close,
    # so the order cannot have been working while that dip printed.
    frame = _frame([*_morning(), (14, 10, 1, 107, 110, 106, 109)])  # never back to 105

    trades, counts = run_backtest(frame, CFG, "NDX100", spread=0.0)

    assert trades == []
    assert counts["no_fill"] == 1


def test_unfilled_limit_is_cancelled_at_the_configured_time() -> None:
    late = _frame([*_morning(),
                   (14, 10, 1, 107, 110, 106, 109),
                   (14, 16, 0, 106, 107, 100, 101)])    # touches 105, but at the cancel time
    assert run_backtest(late, CFG, "NDX100", spread=0.0)[0] == []

    in_time = _frame([*_morning(),
                      (14, 10, 1, 107, 110, 106, 109),
                      (14, 15, 59, 106, 107, 100, 101)])  # one minute earlier
    assert len(run_backtest(in_time, CFG, "NDX100", spread=0.0)[0]) == 1


def test_target_is_held_overnight_rather_than_closed_at_the_session_end() -> None:
    frame = _frame([*_morning(),
                    (14, 10, 1, 107, 108, 104, 106),     # fills at 105
                    (14, 16, 0, 106, 108, 104, 107),     # session end: no exit, keep holding
                    (15, 10, 0, 107, 117, 106, 116)])    # next day: reaches 116.18

    trades, _ = run_backtest(frame, CFG, "NDX100", spread=0.0)

    assert len(trades) == 1
    assert (trades[0].reason, trades[0].exit_time.date()) == ("TP", date(2026, 9, 15))


class TestOnePositionAtATime:
    """Day 15's own setup (106-112 range, 111 close) lands while day 14's trade is open."""

    FRAME = _frame([
        *_morning(14),
        (14, 10, 1, 107, 108, 104, 106),        # day 14 fills at 105
        (14, 11, 0, 106, 108, 104, 107),
        (15, 9, 30, 107, 108, 106, 107),
        (15, 9, 45, 107, 112, 106, 110),        # day 15 range high
        (15, 9, 59, 110, 111, 106, 108),        # day 15 range low -> midline 109
        (15, 10, 0, 108, 111, 107, 111),        # day 15 bias: long
        (15, 10, 1, 111, 111.5, 108, 110),      # touches 109 -> day 15 would fill here
        (15, 12, 0, 110, 117, 109, 116),        # clears both targets
    ])

    def test_second_setup_is_skipped_while_the_first_is_open(self) -> None:
        trades, counts = run_backtest(self.FRAME, CFG, "NDX100", spread=0.0)

        assert [t.day for t in trades] == [date(2026, 9, 14)]
        assert counts["busy"] == 1

    def test_allow_overlap_takes_it_anyway(self) -> None:
        trades, counts = run_backtest(self.FRAME, CFG, "NDX100", spread=0.0, allow_overlap=True)

        assert [t.day for t in trades] == [date(2026, 9, 14), date(2026, 9, 15)]
        assert [t.reason for t in trades] == ["TP", "TP"]
        assert counts["busy"] == 0


def test_day_without_a_0930_bar_produces_no_plan() -> None:
    frame = _frame([(14, 9, 45, 105, 110, 104, 108),
                    (14, 9, 59, 108, 109, 100, 101),
                    (14, 10, 0, 101, 108, 104, 107),
                    (14, 10, 1, 107, 108, 104, 106)])

    trades, counts = run_backtest(frame, CFG, "NDX100", spread=0.0)

    assert trades == []
    assert counts["no_plan"] == 1


def test_trade_still_open_when_the_data_ends_is_reported_not_booked() -> None:
    frame = _frame([*_morning(), (14, 10, 1, 107, 108, 104, 106)])  # fills, never exits

    trades, counts = run_backtest(frame, CFG, "NDX100", spread=0.0)

    assert trades == []
    assert counts["still_open"] == 1


def test_a_narrow_range_pays_more_of_its_r_away_in_spread() -> None:
    # Spread is a fixed number of points, so the SAME spread is a bigger share of a
    # narrow day's risk. This is why the net and gross columns can disagree.
    wide = _frame([*_morning(), (14, 10, 1, 107, 108, 104, 106), (14, 11, 0, 106, 117, 105, 116)])
    narrow = _frame([(14, 9, 30, 104, 105, 103, 104), (14, 9, 45, 104, 105, 103, 104),
                     (14, 9, 59, 104, 105, 103, 104), (14, 10, 0, 104, 105, 104, 104.9),
                     (14, 10, 1, 104.9, 105, 103.5, 104), (14, 11, 0, 104, 110, 104, 109)])

    wide_t = run_backtest(wide, CFG, "NDX100", spread=0.5)[0][0]
    narrow_t = run_backtest(narrow, CFG, "NDX100", spread=0.5)[0][0]

    assert narrow_t.range_points < wide_t.range_points
    assert wide_t.r_gross == pytest.approx(narrow_t.r_gross)   # both are the same 4.23R target
    assert narrow_t.r_net < wide_t.r_net


def test_config_times_are_honoured_end_to_end() -> None:
    # An 09:30-09:45 range (high 106, low 103 from the 09:30 bar alone) with the bias at
    # 09:46: midline 104.5, stop 103 + 0.236 * 3 = 103.708, target 103 + 1.618 * 3 = 107.854.
    cfg = NyFibRetestConfig(range_end=time(9, 45), bias_at=time(9, 46), order_expires=time(10, 0))
    frame = _frame([(14, 9, 30, 104, 106, 103, 105),
                    (14, 9, 45, 105, 107, 105, 106),    # bias bar: 106 > 104.5 -> long
                    (14, 9, 50, 106, 107, 104, 105),    # touches 104.5 -> fills
                    (14, 9, 55, 105, 108, 105, 108)])   # reaches 107.854

    trades, _ = run_backtest(frame, cfg, "NDX100", spread=0.0)

    assert len(trades) == 1
    t = trades[0]
    assert (t.entry, t.stop, t.target) == pytest.approx((104.5, 103.708, 107.854))
    assert (t.reason, t.r_gross) == ("TP", pytest.approx(3.354 / 0.792))
