"""Tests for strategy/first_fvg_window.py -- the 10:00 First FVG rule.

Bars are built in New York wall-clock time on 2026-09-14 (EDT, UTC-4) and handed
to the strategy as UTC, the way MT5Connector.fetch_recent_bars() delivers them.
Every expected price below is worked out by hand from the bar values.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from core.models import Bar, SignalDirection
from strategy.first_fvg_window import FirstFvgWindowConfig, find_plan, setup_id

NY = ZoneInfo("America/New_York")


def _bar(hh: int, mm: int, o: float, h: float, l: float, c: float) -> Bar:
    ts = datetime(2026, 9, 14, hh, mm, tzinfo=NY).astimezone(UTC)
    return Bar(timestamp=ts, open=o, high=h, low=l, close=c, volume=100.0)


def _neutral(hh: int, mm: int, mid: float = 100.0) -> Bar:
    """A bar that overlaps its neighbours, so it can never complete a gap."""
    return _bar(hh, mm, mid, mid + 5, mid - 5, mid)


class TestBullish:
    def test_ten_oclock_bar_as_middle_gives_entry_stop_and_target(self) -> None:
        bars = [
            _bar(9, 30, 90, 95, 85, 92),    # would start an older gap with 10:00 -- must be ignored
            _bar(9, 45, 92, 100, 90, 98),   # candle 1: high 100, low 90
            _bar(10, 0, 98, 121, 97, 120),  # middle
            _bar(10, 15, 119, 125, 105, 124),  # candle 3: low 105 > 100 -> bullish gap
        ]

        plan = find_plan(bars, FirstFvgWindowConfig())

        assert plan is not None
        assert plan.direction == SignalDirection.BUY
        assert plan.entry == 105.0          # near edge = candle 3 low
        assert plan.stop == 90.0            # candle 1 low
        assert plan.target == 150.0         # 105 + 3 * (105 - 90)
        assert plan.c1_time == datetime(2026, 9, 14, 13, 45, tzinfo=UTC)
        assert plan.confirm_close == datetime(2026, 9, 14, 14, 30, tzinfo=UTC)

    def test_gap_starting_before_the_allowed_first_candle_is_ignored(self) -> None:
        bars = [
            _bar(9, 30, 90, 95, 85, 92),    # candle 1 at 09:30 is one bar too early
            _bar(9, 45, 92, 99, 91, 98),
            _bar(10, 0, 98, 110, 97, 108),  # low 97 > 95: a gap, but anchored at 09:30
            _bar(10, 15, 108, 112, 98, 100),  # low 98 <= 99: no gap from the 09:45 candle
            _neutral(10, 30, 100),            # 95-105 overlaps the 10:00 candle: no gap either
        ]

        assert find_plan(bars, FirstFvgWindowConfig()) is None


class TestWindow:
    def test_third_candle_at_eleven_is_outside_the_window(self) -> None:
        bars = [_neutral(9, 45), _neutral(10, 0), _neutral(10, 15), _neutral(10, 30),
                _bar(10, 45, 100, 105, 99, 104),   # candle 1: high 105
                _bar(11, 0, 104, 130, 103, 128),
                _bar(11, 15, 128, 135, 110, 132)]  # low 110 > 105, but starts at 11:00+

        assert find_plan(bars, FirstFvgWindowConfig()) is None

    def test_day_without_a_ten_oclock_bar_is_skipped(self) -> None:
        bars = [
            _bar(10, 15, 92, 100, 90, 98),
            _bar(10, 30, 98, 121, 97, 120),
            _bar(10, 45, 119, 125, 105, 124),  # a valid-looking gap, but no 10:00 bar exists
        ]

        assert find_plan(bars, FirstFvgWindowConfig()) is None


class TestBearish:
    def test_stop_sits_on_candle_one_high(self) -> None:
        bars = [
            _bar(9, 45, 208, 210, 200, 202),   # candle 1: high 210, low 200
            _bar(10, 0, 202, 203, 180, 181),
            _bar(10, 15, 181, 195, 175, 176),  # high 195 < 200 -> bearish gap
        ]

        plan = find_plan(bars, FirstFvgWindowConfig(tp_r=2.0))

        assert plan is not None
        assert plan.direction == SignalDirection.SELL
        assert plan.entry == 195.0          # near edge = candle 3 high
        assert plan.stop == 210.0           # candle 1 high
        assert plan.target == 165.0         # 195 - 2 * (210 - 195)


class TestSetupId:
    def test_id_carries_symbol_ny_date_and_direction(self) -> None:
        bars = [_bar(9, 45, 92, 100, 90, 98), _bar(10, 0, 98, 121, 97, 120),
                _bar(10, 15, 119, 125, 105, 124)]
        plan = find_plan(bars, FirstFvgWindowConfig())
        assert plan is not None

        sid = setup_id("NDX100", plan)

        assert sid == "setup_fvg_window_NDX100_20260914_BUY"
