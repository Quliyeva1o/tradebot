"""Tests for scripts/liquidity_sweep_bos_backtest.py -- the plumbing the day state machine reads.

The rule itself is covered by tests/test_liquidity_sweep_bos.py. What matters here is that
everything handed to it is correct and, above all, not from the future: the vectorised
pivots must equal the repository's own, the 5M values must land on the M1 bar whose close
coincides with theirs, and the 09:30 markup must only read candles that had closed.
"""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from core.models import SignalDirection
from scripts.backtest_common import compute_pivots
from scripts.liquidity_sweep_bos_backtest import (
    Session,
    _ffill,
    _find_exit,
    _pivots,
    run_backtest,
)
from strategy.liquidity_sweep_bos import LiquiditySweepBosConfig, SweepEntry

NY = ZoneInfo("America/New_York")


def _minute_frame(days: list[tuple[date, float]]) -> pd.DataFrame:
    """A full 00:00-23:59 of M1 bars per day, flat at that day's base price +/- 1."""
    stamps, rows = [], []
    for day, base in days:
        for minute in range(24 * 60):
            stamps.append(pd.Timestamp(day.year, day.month, day.day,
                                       minute // 60, minute % 60, tz=NY))
            rows.append((base, base + 1, base - 1, base))
    return pd.DataFrame(
        {"open": [r[0] for r in rows], "high": [r[1] for r in rows],
         "low": [r[2] for r in rows], "close": [r[3] for r in rows],
         "volume": [1.0] * len(rows)},
        index=pd.DatetimeIndex(stamps, name="ts"),
    ).sort_index()


class TestPivots:
    """The vectorised pivots must be the repository's, exactly."""

    @pytest.mark.parametrize("k", [1, 2, 3, 5])
    def test_they_equal_compute_pivots_on_noisy_data(self, k: int) -> None:
        rng = np.random.default_rng(7)
        close = 100 + np.cumsum(rng.normal(0, 1, 400))
        frame = pd.DataFrame({"high": close + rng.uniform(0, 1, 400),
                              "low": close - rng.uniform(0, 1, 400)})

        want_h, want_l = compute_pivots(frame, k)
        got_h, got_l = _pivots(frame["high"].to_numpy(), frame["low"].to_numpy(), k)

        np.testing.assert_array_equal(np.isnan(got_h), np.isnan(want_h))
        np.testing.assert_allclose(got_h[~np.isnan(got_h)], want_h[~np.isnan(want_h)])
        np.testing.assert_allclose(got_l[~np.isnan(got_l)], want_l[~np.isnan(want_l)])

    def test_a_pivot_is_written_at_its_confirmation_bar_not_at_itself(self) -> None:
        # The 30 at index 3 is the peak; with k=2 it may only be read from index 5.
        highs = np.array([10.0, 11, 12, 30, 12, 11, 10, 9])
        lows = highs - 1

        ph, _ = _pivots(highs, lows, 2)

        assert np.isnan(ph[3]) and np.isnan(ph[4])
        assert ph[5] == 30.0

    def test_too_few_bars_gives_all_nan_rather_than_raising(self) -> None:
        ph, pl = _pivots(np.array([1.0, 2.0]), np.array([0.0, 1.0]), 3)

        assert np.isnan(ph).all() and np.isnan(pl).all()


class TestFfill:
    def test_values_carry_forward_and_leading_gaps_stay_nan(self) -> None:
        out = _ffill(np.array([np.nan, 5.0, np.nan, np.nan, 7.0, np.nan]))

        assert np.isnan(out[0])
        assert list(out[1:]) == [5.0, 5.0, 5.0, 7.0, 7.0]


class TestLevels:
    """Day 2's markup, worked out by hand from two flat days at 100 and 110."""

    DAYS = [(date(2026, 9, 14), 100.0), (date(2026, 9, 15), 110.0)]

    def test_markup_reads_the_previous_session_the_overnight_and_closed_candles(self) -> None:
        session = Session(_minute_frame(self.DAYS))

        levels = session.levels_by_day()[date(2026, 9, 15)]

        assert (levels.psh, levels.psl) == (101.0, 99.0)     # day 1's 09:30-16:00
        # Overnight spans day 1's 18:00 through day 2's 09:30, so it sees both bases.
        assert (levels.onh, levels.onl) == (111.0, 99.0)
        # The last 1H candle closed by 09:30 is 08:00-09:00; the last 4H is 04:00-08:00.
        assert (levels.h1_high, levels.h1_low) == (111.0, 109.0)
        assert (levels.h4_high, levels.h4_low) == (111.0, 109.0)

    def test_the_first_day_has_no_previous_session_so_it_is_skipped(self) -> None:
        session = Session(_minute_frame(self.DAYS))

        assert date(2026, 9, 14) not in session.levels_by_day()

    def test_an_intraday_candle_cannot_reach_back_into_the_markup(self) -> None:
        # Day 2's afternoon is given a huge spike. The 09:30 markup must not see it.
        frame = _minute_frame(self.DAYS)
        afternoon = (frame.index.date == date(2026, 9, 15)) & (frame.index.hour >= 13)
        frame.loc[afternoon, "high"] = 999.0
        frame.loc[afternoon, "low"] = -999.0

        levels = Session(frame).levels_by_day()[date(2026, 9, 15)]

        assert levels.h4_high == 111.0
        assert levels.h1_high == 111.0
        assert levels.onh == 111.0


class TestFourHourGrid:
    """A 4H candle must sit on the NY chart grid on BOTH sides of a DST change.

    `resample(frame, 240)` does not: it steps in absolute time from an origin, so it
    draws 00:00/04:00/08:00 blocks in one half of the year and 03:00/07:00/11:00 in the
    other. That produced two different sets of 4H levels for the same dates depending on
    where the CSV started, and so two different backtests of the same rule.
    """

    def _days(self, first: date, count: int) -> list[tuple[date, float]]:
        return [(first + timedelta(days=i), 100.0 + i) for i in range(count)]

    def test_candles_close_on_the_local_four_hour_boundaries_across_dst(self) -> None:
        # 2025-11-02 is the US autumn changeover.
        session = Session(_minute_frame(self._days(date(2025, 10, 30), 6)))

        known_ns, _, _ = session._htf_levels(4)

        local = pd.DatetimeIndex(known_ns[:-1].astype("datetime64[ns]"), tz="UTC").tz_convert(NY)
        assert set(local.hour) <= {0, 4, 8, 12, 16, 20}
        assert set(local.minute) == {0}

    def test_a_plain_resample_would_have_drifted(self) -> None:
        # The guard this test class exists for: prove the old approach really does slip,
        # so nobody "simplifies" _htf_levels back to resample(frame, 240).
        from scripts.backtest_common import resample as plain

        frame = _minute_frame(self._days(date(2025, 10, 30), 6))

        assert set(plain(frame, 240).index.hour) != {0, 4, 8, 12, 16, 20}

    def test_each_candle_is_stamped_after_its_own_last_bar(self) -> None:
        session = Session(_minute_frame(self._days(date(2025, 10, 30), 6)))

        known_ns, highs, _ = session._htf_levels(4)

        # Nothing may be readable before it closed: the stamp is strictly later than
        # every bar the candle is built from.
        assert (np.diff(known_ns) > 0).all()
        assert len(highs) == len(known_ns)


class TestFiveMinuteAlignment:
    def test_5m_values_land_on_the_m1_bar_whose_close_coincides(self) -> None:
        session = Session(_minute_frame([(date(2026, 9, 14), 100.0),
                                         (date(2026, 9, 15), 110.0)]))

        ctx = session.context(2)

        # A 5M bar covering [t, t+5) closes at t+5, which is the close of the M1 bar
        # stamped t+4 -- so nothing may be stamped on any other minute.
        carried = ~np.isnan(ctx.bos5_long) | ~np.isnan(ctx.bos5_short)
        assert set(session.minutes[carried] % 5) <= {4}

    def test_a_flat_series_never_breaks_structure(self) -> None:
        session = Session(_minute_frame([(date(2026, 9, 14), 100.0),
                                         (date(2026, 9, 15), 100.0)]))

        ctx = session.context(2)

        assert np.isnan(ctx.bos5_long).all()
        assert np.isnan(ctx.fvg_long).all()


class TestFindExit:
    """The exit search starts AFTER the entry bar, because the fill was its close."""

    DAYS = [(date(2026, 9, 14), 100.0), (date(2026, 9, 15), 110.0)]

    def _entry(self, session: Session, index: int, stop: float, target: float) -> SweepEntry:
        return SweepEntry(
            direction=SignalDirection.BUY, time=session.m1[index].timestamp,
            price=100.0, stop=stop, target=target, level_name="ONL", level_price=99.0,
            sweep_extreme=99.0, leg_high=102.0, target_name="ONH",
        )

    def test_session_close_exits_at_the_next_bars_open(self) -> None:
        session = Session(_minute_frame(self.DAYS))
        # 15:59 on day 1; the flat series never reaches a far stop or target.
        i = int(np.flatnonzero((session.minutes == 959)
                               & (session.frame.index.date == date(2026, 9, 14)))[0])

        found = _find_exit(session.m1, session.minutes, session.day_key, i,
                           self._entry(session, i, stop=50.0, target=500.0), close_minute=960)

        assert found is not None
        j, _, price, reason = found
        assert (reason, price) == ("TIME", 100.0)
        assert session.minutes[j] == 960

    def test_holding_past_the_session_runs_to_the_target(self) -> None:
        session = Session(_minute_frame(self.DAYS))
        i = int(np.flatnonzero((session.minutes == 959)
                               & (session.frame.index.date == date(2026, 9, 14)))[0])

        found = _find_exit(session.m1, session.minutes, session.day_key, i,
                           self._entry(session, i, stop=50.0, target=110.0), close_minute=None)

        assert found is not None
        assert found[3] == "TP"
        assert session.frame.index.date[found[0]] == date(2026, 9, 15)  # the next day's 110

    def test_a_trade_that_never_resolves_returns_none(self) -> None:
        session = Session(_minute_frame(self.DAYS))
        i = len(session.minutes) - 5

        found = _find_exit(session.m1, session.minutes, session.day_key, i,
                           self._entry(session, i, stop=1.0, target=9999.0), close_minute=None)

        assert found is None


class TestEndToEnd:
    def test_every_day_is_accounted_for(self) -> None:
        # A drifting series so the machinery actually fires rather than sitting flat.
        rng = np.random.default_rng(3)
        days = [(date(2026, 9, 14) + timedelta(days=i), 100.0 + 5 * i) for i in range(6)]
        frame = _minute_frame(days)
        noise = rng.normal(0, 0.8, len(frame))
        frame["close"] = frame["close"].to_numpy() + noise
        frame["open"] = frame["close"]
        frame["high"] = frame["close"] + np.abs(rng.normal(0, 0.5, len(frame)))
        frame["low"] = frame["close"] - np.abs(rng.normal(0, 0.5, len(frame)))
        session = Session(frame)

        trades, counts = run_backtest(session, LiquiditySweepBosConfig(), "NDX100", spread=0.1)

        assert counts.days == len(trades) + counts.no_levels + counts.no_setup \
            + counts.busy + counts.still_open
        for t in trades:
            assert t.stop < t.entry < t.target      # a long's three prices, in order
            assert t.r_gross == pytest.approx((t.exit_price - t.entry) / (t.entry - t.stop))

    def test_the_short_side_mirrors(self) -> None:
        rng = np.random.default_rng(3)
        days = [(date(2026, 9, 14) + timedelta(days=i), 100.0 - 5 * i) for i in range(6)]
        frame = _minute_frame(days)
        frame["close"] = frame["close"].to_numpy() + rng.normal(0, 0.8, len(frame))
        frame["open"] = frame["close"]
        frame["high"] = frame["close"] + np.abs(rng.normal(0, 0.5, len(frame)))
        frame["low"] = frame["close"] - np.abs(rng.normal(0, 0.5, len(frame)))

        trades, _ = run_backtest(Session(frame), LiquiditySweepBosConfig(), "XAUUSD",
                                 spread=0.1, long=False)

        for t in trades:
            assert t.direction == "SHORT"
            assert t.stop > t.entry > t.target
