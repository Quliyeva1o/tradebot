"""Tests for strategy/liquidity_sweep_bos.py -- the four-gate sweep/BOS model.

Bars are built in New York wall-clock time on 2026-09-14 (EDT, UTC-4) and handed to the
strategy as UTC, the way MT5Connector.fetch_recent_bars() delivers them. Every test uses
the same markup, so the hand-worked numbers are the same throughout:

    ONL 100 (the nearest sell-side level), PSL 95, H1L 90, H4L 80
    ONH 120 (the nearest buy-side level), PSH 125, H1H 130, H4H 140
"""

from datetime import UTC, datetime, time

import pytest

from core.models import Bar, SignalDirection
from strategy.liquidity_sweep_bos import (
    NY,
    STRATEGY_TAG,
    DayState,
    Levels,
    LiquiditySweepBosConfig,
    RetraceMode,
    StopMode,
    TargetMode,
    setup_id,
)

LEVELS = Levels(h4_high=140, h4_low=80, h1_high=130, h1_low=90,
                psh=125, psl=95, onh=120, onl=100)
CFG = LiquiditySweepBosConfig()


def _bar(hh: int, mm: int, o: float, h: float, low: float, c: float) -> Bar:
    ts = datetime(2026, 9, 14, hh, mm, tzinfo=NY).astimezone(UTC)
    return Bar(timestamp=ts, open=o, high=h, low=low, close=c, volume=100.0)


class Feed:
    """Drives a DayState, remembering the entry it produced."""

    def __init__(self, cfg: LiquiditySweepBosConfig = CFG, *, long: bool = True) -> None:
        self.state = DayState(LEVELS, cfg, long=long)
        self.entry = None

    def send(self, bar: Bar, **kw) -> None:
        if self.entry is None:
            self.entry = self.state.on_bar(bar, **kw)


def _through_bos5(feed: Feed, *, sweep_low: float = 96.0) -> None:
    """Sweep ONL down to `sweep_low`, reclaim it, then confirm a 5M BOS with a 112 leg."""
    feed.send(_bar(9, 40, 105, 106, sweep_low, 99))   # sweeps ONL (and PSL at 95 if deep)
    feed.send(_bar(9, 41, 99, 103, 98, 102))          # closes back above ONL -> reclaimed
    feed.send(_bar(9, 44, 102, 112, 101, 111), bos5_leg_high=112.0)


class TestGates:
    def test_full_sequence_produces_an_entry(self) -> None:
        feed = Feed()
        _through_bos5(feed)
        feed.send(_bar(9, 50, 111, 111, 104, 105))    # retraces to the 104 equilibrium
        feed.send(_bar(9, 55, 105, 109, 105, 108), m1_swing=107.0)  # 1M BOS: closes above 107

        assert feed.entry is not None
        assert feed.entry.direction == SignalDirection.BUY
        assert feed.entry.price == 108.0              # a market fill at the BOS close
        assert feed.entry.level_name == "ONL"
        assert feed.entry.sweep_extreme == 96.0
        assert feed.entry.leg_high == 112.0

    def test_sweep_without_a_reclaim_is_not_a_setup(self) -> None:
        feed = Feed()
        feed.send(_bar(9, 40, 105, 106, 96, 97))      # sweeps ONL, closes below it
        feed.send(_bar(9, 44, 97, 112, 96, 111), bos5_leg_high=112.0)
        feed.send(_bar(9, 50, 111, 111, 104, 105))
        feed.send(_bar(9, 55, 105, 109, 105, 108), m1_swing=107.0)

        assert feed.entry is None

    def test_reclaim_without_a_5m_bos_is_not_a_setup(self) -> None:
        feed = Feed()
        feed.send(_bar(9, 40, 105, 106, 96, 99))
        feed.send(_bar(9, 41, 99, 103, 98, 102))      # reclaimed
        feed.send(_bar(9, 50, 102, 112, 101, 111))    # no bos5_leg_high handed over
        feed.send(_bar(9, 55, 105, 109, 105, 108), m1_swing=107.0)

        assert feed.entry is None

    def test_bos5_without_a_retracement_is_not_a_setup(self) -> None:
        feed = Feed()
        _through_bos5(feed)
        feed.send(_bar(9, 50, 111, 115, 110, 114), m1_swing=107.0)  # never reaches 104

        assert feed.entry is None

    def test_retracement_without_a_1m_bos_is_not_a_setup(self) -> None:
        feed = Feed()
        _through_bos5(feed)
        feed.send(_bar(9, 50, 111, 111, 104, 105))
        feed.send(_bar(9, 55, 105, 106, 104, 105), m1_swing=107.0)  # close stays below 107

        assert feed.entry is None

    def test_a_deeper_sweep_during_the_reclaim_is_not_an_invalidation(self) -> None:
        # The sweep running further is the sweep, not its failure.
        feed = Feed()
        feed.send(_bar(9, 40, 105, 106, 98, 99))
        feed.send(_bar(9, 41, 99, 100, 94, 96))       # deeper, still below ONL
        feed.send(_bar(9, 42, 96, 103, 95, 102))      # now reclaims
        feed.send(_bar(9, 44, 102, 112, 101, 111), bos5_leg_high=112.0)
        feed.send(_bar(9, 50, 111, 111, 103, 104))    # equilibrium is (112 + 94) / 2 = 103
        feed.send(_bar(9, 55, 104, 109, 104, 108), m1_swing=107.0)

        assert feed.entry is not None
        assert feed.entry.sweep_extreme == 94.0

    def test_trading_back_through_the_sweep_low_kills_the_setup(self) -> None:
        feed = Feed()
        _through_bos5(feed)
        feed.send(_bar(9, 50, 111, 111, 95, 100))     # back below the 96 sweep low
        feed.send(_bar(9, 55, 100, 109, 100, 108), m1_swing=107.0)

        assert feed.entry is None


class TestLevelChoice:
    def test_the_deepest_swept_level_is_the_one_reclaimed(self) -> None:
        # One bar takes ONL (100), PSL (95) and H1L (90): the reclaim must clear H1L.
        feed = Feed()
        feed.send(_bar(9, 40, 105, 106, 89, 92))      # closes above H1L but below PSL
        feed.send(_bar(9, 44, 92, 112, 91, 111), bos5_leg_high=112.0)
        feed.send(_bar(9, 50, 111, 111, 100, 102))    # equilibrium is (112 + 89) / 2 = 100.5
        feed.send(_bar(9, 55, 102, 109, 101, 108), m1_swing=107.0)

        assert feed.entry is not None
        assert feed.entry.level_name == "H1L"
        assert feed.entry.level_price == 90.0


class TestRetraceModes:
    def _feed_to_zone(self, cfg: LiquiditySweepBosConfig, retrace: Bar, **kw) -> Feed:
        feed = Feed(cfg)
        _through_bos5(feed)
        feed.send(retrace, **kw)
        feed.send(_bar(9, 55, 105, 109, 104, 108), m1_swing=107.0, **kw)
        return feed

    def test_fvg_mode_ignores_a_touch_of_equilibrium_alone(self) -> None:
        cfg = LiquiditySweepBosConfig(retrace_mode=RetraceMode.FVG)

        feed = self._feed_to_zone(cfg, _bar(9, 50, 111, 111, 104, 105), fvg_edge=None)

        assert feed.entry is None   # 104 is the equilibrium, but this mode needs the gap

    def test_fvg_mode_enters_on_the_gap(self) -> None:
        cfg = LiquiditySweepBosConfig(retrace_mode=RetraceMode.FVG)

        feed = self._feed_to_zone(cfg, _bar(9, 50, 111, 111, 107, 108), fvg_edge=108.0)

        assert feed.entry is not None

    def test_a_gap_outside_the_leg_is_ignored(self) -> None:
        # 60 is below the 96 sweep low, so it is some older imbalance, not this leg's.
        cfg = LiquiditySweepBosConfig(retrace_mode=RetraceMode.FVG)

        feed = self._feed_to_zone(cfg, _bar(9, 50, 111, 111, 70, 105), fvg_edge=60.0)

        assert feed.entry is None

    def test_either_mode_takes_the_shallower_of_the_two(self) -> None:
        # The gap at 108 is met before the 104 equilibrium on the way down.
        cfg = LiquiditySweepBosConfig(retrace_mode=RetraceMode.EITHER)

        feed = self._feed_to_zone(cfg, _bar(9, 50, 111, 111, 107, 108), fvg_edge=108.0)

        assert feed.entry is not None


class TestStopAndTarget:
    def _entry(self, cfg: LiquiditySweepBosConfig):
        feed = Feed(cfg)
        _through_bos5(feed)
        feed.send(_bar(9, 50, 111, 111, 104, 105))
        feed.send(_bar(9, 55, 105, 109, 105, 108), m1_swing=107.0)
        return feed.entry

    def test_sweep_stop_sits_below_the_swept_wick(self) -> None:
        entry = self._entry(LiquiditySweepBosConfig())

        assert entry is not None
        assert entry.stop == pytest.approx(96.0 - 0.05 * 12.0)   # 12 = 108 - 96
        assert entry.risk == pytest.approx(108.0 - 95.4)

    def test_pullback_stop_sits_below_the_retracement_low(self) -> None:
        cfg = LiquiditySweepBosConfig(stop_mode=StopMode.PULLBACK_EXTREME)

        entry = self._entry(cfg)

        assert entry is not None
        assert entry.stop == pytest.approx(104.0 - 0.05 * 4.0)   # 4 = 108 - 104
        assert entry.risk < 13.0                                  # much tighter than the sweep stop

    def test_target_is_the_nearest_buy_side_level_above_entry(self) -> None:
        entry = self._entry(LiquiditySweepBosConfig())

        assert entry is not None
        assert (entry.target, entry.target_name) == (120.0, "ONH")   # ONH before PSH/H1H/H4H

    def test_fixed_r_target_is_a_multiple_of_the_trades_own_risk(self) -> None:
        cfg = LiquiditySweepBosConfig(target_mode=TargetMode.FIXED_R, target_r=2.0)

        entry = self._entry(cfg)

        assert entry is not None
        assert entry.target == pytest.approx(108.0 + 2 * entry.risk)
        assert entry.target_name == "2.0R"

    def test_the_leg_stands_in_when_every_marked_level_is_behind_price(self) -> None:
        feed = Feed()
        feed.send(_bar(9, 40, 145, 146, 96, 99))      # sweeps ONL from far above every high
        feed.send(_bar(9, 41, 99, 103, 98, 102))
        feed.send(_bar(9, 44, 102, 150, 101, 149), bos5_leg_high=150.0)
        feed.send(_bar(9, 50, 149, 149, 123, 124))    # equilibrium is (150 + 96) / 2 = 123
        feed.send(_bar(9, 55, 124, 145, 124, 144), m1_swing=140.5)

        assert feed.entry is not None
        assert (feed.entry.target, feed.entry.target_name) == (150.0, "LEG")


class TestShortMirror:
    def test_a_sweep_of_the_overnight_high_sets_up_a_short(self) -> None:
        feed = Feed(long=False)
        feed.send(_bar(9, 40, 115, 124, 114, 121))    # sweeps ONH (120), closes above it
        feed.send(_bar(9, 41, 121, 122, 117, 118))    # closes back below -> reclaimed
        feed.send(_bar(9, 44, 118, 119, 108, 109), bos5_leg_high=108.0)
        feed.send(_bar(9, 50, 109, 116, 109, 115))    # equilibrium is (108 + 124) / 2 = 116
        feed.send(_bar(9, 55, 115, 115, 111, 112), m1_swing=113.0)

        assert feed.entry is not None
        assert feed.entry.direction == SignalDirection.SELL
        assert feed.entry.level_name == "ONH"
        assert feed.entry.sweep_extreme == 124.0
        assert feed.entry.target_name == "ONL"        # the nearest sell-side level below
        assert feed.entry.stop > feed.entry.price


class TestDeadlinesAndSession:
    def test_nothing_starts_before_the_session_opens(self) -> None:
        feed = Feed()
        feed.send(_bar(9, 0, 105, 106, 96, 99))       # a sweep, but pre-session
        feed.send(_bar(9, 1, 99, 103, 98, 102))
        feed.send(_bar(9, 44, 102, 112, 101, 111), bos5_leg_high=112.0)
        feed.send(_bar(9, 50, 111, 111, 104, 105))
        feed.send(_bar(9, 55, 105, 109, 105, 108), m1_swing=107.0)

        assert feed.entry is None

    def test_the_reclaim_deadline_ends_the_day(self) -> None:
        cfg = LiquiditySweepBosConfig(reclaim_bars=2)
        feed = Feed(cfg)
        feed.send(_bar(9, 40, 105, 106, 96, 97))
        for minute in (41, 42, 43):
            feed.send(_bar(9, minute, 97, 99, 96, 97))   # still below ONL
        feed.send(_bar(9, 45, 97, 103, 96, 102))         # a reclaim, but too late
        feed.send(_bar(9, 46, 102, 112, 101, 111), bos5_leg_high=112.0)
        feed.send(_bar(9, 50, 111, 111, 104, 105))
        feed.send(_bar(9, 55, 105, 109, 105, 108), m1_swing=107.0)

        assert feed.entry is None

    def test_nothing_is_entered_after_the_session_closes(self) -> None:
        feed = Feed()
        _through_bos5(feed)
        feed.send(_bar(9, 50, 111, 111, 104, 105))
        feed.send(_bar(16, 0, 105, 109, 105, 108), m1_swing=107.0)

        assert feed.entry is None

    def test_only_one_trade_a_day(self) -> None:
        feed = Feed()
        _through_bos5(feed)
        feed.send(_bar(9, 50, 111, 111, 104, 105))
        feed.send(_bar(9, 55, 105, 109, 105, 108), m1_swing=107.0)
        first = feed.entry
        feed.entry = None
        feed.send(_bar(9, 56, 108, 115, 107, 114), m1_swing=107.0)

        assert first is not None
        assert feed.entry is None


class TestConfig:
    def test_a_session_that_ends_before_it_starts_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            LiquiditySweepBosConfig(session_start=time(16, 0), session_end=time(9, 30))

    def test_a_non_positive_pivot_strength_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            LiquiditySweepBosConfig(pivot_strength=0)


class TestLevels:
    def test_a_long_sweeps_the_lows_and_targets_the_highs(self) -> None:
        assert [n for n, _ in LEVELS.sweep_side(long=True)] == ["H4L", "H1L", "PSL", "ONL"]
        assert [n for n, _ in LEVELS.target_side(long=True)] == ["H4H", "H1H", "PSH", "ONH"]

    def test_a_short_sweeps_the_highs_and_targets_the_lows(self) -> None:
        assert LEVELS.sweep_side(long=False) == LEVELS.highs()
        assert LEVELS.target_side(long=False) == LEVELS.lows()


class TestSetupId:
    def test_tag_fits_the_twenty_characters_mt5_keeps(self) -> None:
        assert len(STRATEGY_TAG) <= 20

    def test_id_carries_the_date_and_side(self) -> None:
        feed = Feed()
        _through_bos5(feed)
        feed.send(_bar(9, 50, 111, 111, 104, 105))
        feed.send(_bar(9, 55, 105, 109, 105, 108), m1_swing=107.0)

        assert feed.entry is not None
        assert setup_id("NDX100", feed.entry) == f"{STRATEGY_TAG}_NDX100_20260914_BUY"
