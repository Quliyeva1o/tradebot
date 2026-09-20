"""Tests for scripts/or_fib_pullback_backtest.py -- one bot's trades, priced like the paper broker.

Every day uses the same 100-110 opening range (NY wall clock, EDT) and a 09:45 close of
112, so: long break, entry 103.82, stop 101.94, risk 1.88, target = the morning high of
112. Expected R values are worked out by hand.
"""

from datetime import date, time
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from scripts.or_fib_pullback_backtest import Session, run_backtest
from strategy.or_fib_pullback import OrFibPullbackConfig

NY = ZoneInfo("America/New_York")
CFG = OrFibPullbackConfig(atr_mult=0.0)
RISK = 1.88  # 103.82 - 101.94

Row = tuple[int, int, int, float, float, float, float]


def _frame(rows: list[Row]) -> pd.DataFrame:
    """(day, hh, mm, open, high, low, close) in NY wall clock -> a load_m1()-shaped frame."""
    index = pd.DatetimeIndex(
        [pd.Timestamp(2026, 9, d, hh, mm, tz=NY) for d, hh, mm, *_ in rows], name="ts"
    )
    return pd.DataFrame(
        {"open": [r[3] for r in rows], "high": [r[4] for r in rows],
         "low": [r[5] for r in rows], "close": [r[6] for r in rows],
         "volume": [1.0] * len(rows)},
        index=index,
    ).sort_index()


def _session(rows: list[Row]) -> Session:
    return Session(_frame(rows))


def _morning(day: int = 14) -> list[Row]:
    return [
        (day, 9, 30, 105, 106, 104, 105),
        (day, 9, 35, 105, 110, 104, 109),   # the range high
        (day, 9, 44, 109, 109, 100, 101),   # the range low
        (day, 9, 45, 101, 112, 101, 112),   # closes above 110 -> long break, morning high 112
    ]


def _quiet_pre_open(day: int = 14) -> list[Row]:
    """06:00-09:29 of flat 1-point bars, so ATR(14) on M15 is about 1.0 by the open."""
    return [(day, 6 + m // 60, m % 60, 105, 105.5, 104.5, 105) for m in range(210)]


def test_fills_at_the_pocket_and_books_the_target_minus_spread() -> None:
    session = _session([*_morning(),
                        (14, 9, 46, 112, 113, 103, 104),    # dips to 103.82 -> fills
                        (14, 9, 47, 104, 112, 104, 111)])   # reaches the 112 target

    trades, counts = run_backtest(session, CFG, "NDX100", spread=0.188)

    assert len(trades) == 1
    t = trades[0]
    assert (t.day, t.direction) == (date(2026, 9, 14), "LONG")
    assert (t.entry, t.stop, t.target) == pytest.approx((103.82, 101.94, 112.0))
    assert (t.range_high, t.range_low) == (110.0, 100.0)
    assert t.reason == "TP"
    assert t.r_gross == pytest.approx(8.18 / RISK)
    assert t.r_net == pytest.approx(8.18 / RISK - 0.1)   # 0.188 of spread over 1.88 of risk
    assert t.bars_held == 1
    assert t.setup_id == "setup_or_fib_NDX100_20260914_BUY"
    assert counts.days == 1


def test_the_entry_bars_own_high_cannot_book_the_target() -> None:
    # The entry bar opened at 112 and fell to the pocket, so its 113 high printed BEFORE
    # the limit filled. Booking a 112 target off it would be a take-profit in the past.
    session = _session([*_morning(),
                        (14, 9, 46, 112, 113, 103, 104),
                        (14, 9, 47, 104, 105, 104, 105)])   # no exit; still open at the end

    trades, counts = run_backtest(session, CFG, "NDX100", spread=0.0)

    assert trades == []
    assert counts.still_open == 1


def test_the_stop_does_fill_on_the_entry_bar() -> None:
    session = _session([*_morning(),
                        (14, 9, 46, 112, 113, 101, 102)])   # fills at 103.82, then through 101.94

    trades, _ = run_backtest(session, CFG, "NDX100", spread=0.0)

    assert len(trades) == 1
    assert (trades[0].reason, trades[0].r_gross) == ("SL", pytest.approx(-1.0))


def test_short_break_targets_the_morning_low() -> None:
    morning = [*_morning()[:3], (14, 9, 45, 101, 101, 98, 99)]  # closes below 100 -> short
    session = _session([*morning,
                        (14, 9, 46, 99, 107, 99, 100),          # rallies to 106.18 -> fills
                        (14, 9, 47, 100, 100, 98, 98)])         # reaches the 98 target

    trades, _ = run_backtest(session, CFG, "NDX100", spread=0.0)

    assert len(trades) == 1
    t = trades[0]
    assert t.direction == "SHORT"
    assert (t.entry, t.stop, t.target) == pytest.approx((106.18, 108.06, 98.0))
    assert t.reason == "TP"


def test_open_trade_is_closed_at_the_cancel_time_at_that_bars_open() -> None:
    session = _session([*_morning(),
                        (14, 9, 46, 112, 113, 103, 104),    # fills at 103.82
                        (14, 11, 59, 104, 105, 103, 104),   # still nothing
                        (14, 12, 0, 106, 120, 90, 95)])     # the clock: exit at 106, not 120 or 90

    trades, _ = run_backtest(session, CFG, "NDX100", spread=0.0)

    assert len(trades) == 1
    t = trades[0]
    assert (t.reason, t.exit_price) == ("TIME", 106.0)
    assert t.r_gross == pytest.approx((106.0 - 103.82) / RISK)


def test_holding_past_the_cancel_time_reaches_the_target_instead() -> None:
    rows = [*_morning(),
            (14, 9, 46, 112, 113, 103, 104),
            (14, 12, 0, 106, 108, 105, 107),
            (14, 14, 0, 107, 112, 107, 112)]    # the target, hours after midday
    held = OrFibPullbackConfig(atr_mult=0.0, close_at=None)

    timed, _ = run_backtest(_session(rows), CFG, "NDX100", spread=0.0)
    holding, _ = run_backtest(_session(rows), held, "NDX100", spread=0.0)

    assert timed[0].reason == "TIME"
    assert holding[0].reason == "TP"
    assert holding[0].r_gross > timed[0].r_gross


def test_an_r_target_books_exactly_that_many_r_gross() -> None:
    # Fill 103.82, stop 101.94, risk 1.88 -> the 1R target is 105.70.
    session = _session([*_morning(),
                        (14, 9, 46, 112, 113, 103, 104),
                        (14, 9, 47, 104, 106, 104, 105)])   # trades through 105.70
    cfg = OrFibPullbackConfig(atr_mult=0.0, target_r=1.0)

    trades, _ = run_backtest(session, cfg, "NDX100", spread=0.188)

    assert len(trades) == 1
    t = trades[0]
    assert (t.target, t.reason) == (pytest.approx(105.70), "TP")
    assert t.r_gross == pytest.approx(1.0)
    assert t.r_net == pytest.approx(0.9)


class TestAtrFilter:
    ROWS = [*_morning(), (14, 9, 46, 112, 113, 103, 104), (14, 9, 47, 104, 112, 104, 111)]

    def test_a_day_with_no_atr_history_is_filtered_rather_than_traded(self) -> None:
        cfg = OrFibPullbackConfig(atr_mult=1.0)

        trades, counts = run_backtest(_session(self.ROWS), cfg, "NDX100", spread=0.0)

        assert trades == []
        assert (counts.atr_filtered, counts.days) == (1, 1)

    def test_a_range_that_clears_the_threshold_trades(self) -> None:
        session = _session([*_quiet_pre_open(), *self.ROWS])   # ATR about 1.0, range 10

        trades, counts = run_backtest(session, OrFibPullbackConfig(atr_mult=1.0), "NDX100", 0.0)

        assert len(trades) == 1
        assert counts.atr_filtered == 0
        assert trades[0].atr == pytest.approx(1.0, abs=0.2)

    def test_a_range_that_misses_the_threshold_is_filtered(self) -> None:
        session = _session([*_quiet_pre_open(), *self.ROWS])   # the same day, a stricter bar

        trades, counts = run_backtest(session, OrFibPullbackConfig(atr_mult=20.0), "NDX100", 0.0)

        assert trades == []
        assert counts.atr_filtered == 1


def test_a_day_that_breaks_but_never_pulls_back_counts_as_no_entry() -> None:
    session = _session([*_morning(), (14, 9, 46, 112, 118, 111, 117)])

    trades, counts = run_backtest(session, CFG, "NDX100", spread=0.0)

    assert trades == []
    assert counts.no_entry == 1


def test_day_without_the_0930_bar_counts_as_no_range() -> None:
    session = _session([*_morning()[1:], (14, 9, 46, 112, 113, 103, 104)])

    trades, counts = run_backtest(session, CFG, "NDX100", spread=0.0)

    assert trades == []
    assert counts.no_range == 1


def test_second_day_is_skipped_while_the_first_trade_is_still_open() -> None:
    held = OrFibPullbackConfig(atr_mult=0.0, close_at=None)
    # Day 15 carries its own 104-110 range, deliberately inside day 14's stop (101.94)
    # and target (112) so that trade is still running when day 15's entry comes up.
    rows = [*_morning(14), (14, 9, 46, 112, 113, 103, 104),   # day 14 fills, no exit
            (15, 9, 30, 106, 107, 105, 106),
            (15, 9, 35, 106, 110, 105, 109),                  # day 15 range high
            (15, 9, 44, 109, 109, 104, 105),                  # day 15 range low
            (15, 9, 45, 105, 111.5, 105, 111.5),              # day 15 break, morning high 111.5
            (15, 9, 46, 111.5, 111.5, 106, 107),              # day 15 would fill at 106.292
            (15, 13, 0, 107, 113, 107, 112)]                  # clears both targets

    skipped, counts = run_backtest(_session(rows), held, "NDX100", spread=0.0)
    overlapped, _ = run_backtest(_session(rows), held, "NDX100", spread=0.0, allow_overlap=True)

    assert [t.day for t in skipped] == [date(2026, 9, 14)]
    assert counts.busy == 1
    assert [t.day for t in overlapped] == [date(2026, 9, 14), date(2026, 9, 15)]


def test_config_times_are_honoured_end_to_end() -> None:
    # A 09:30-09:35 range (high 106, low 104 from the 09:30 bar alone): entry
    # 106 - 0.618 * 2 = 104.764, zone 104.428, stop 104.428 - 0.02 * 2 = 104.388.
    cfg = OrFibPullbackConfig(atr_mult=0.0, range_end=time(9, 35),
                              entry_deadline=time(10, 0), close_at=time(10, 0))
    session = _session([(14, 9, 30, 105, 106, 104, 105),
                        (14, 9, 35, 105, 108, 105, 107),    # closes above 106 -> break
                        (14, 9, 36, 107, 107, 104.5, 105),  # dips to 104.764 -> fills
                        (14, 9, 37, 105, 108, 105, 108)])   # reaches the 108 morning high

    trades, _ = run_backtest(session, cfg, "NDX100", spread=0.0)

    assert len(trades) == 1
    t = trades[0]
    assert (t.entry, t.stop, t.target) == pytest.approx((104.764, 104.388, 108.0))
    assert (t.reason, t.r_gross) == ("TP", pytest.approx(3.236 / 0.376))
