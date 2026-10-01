"""strategy/gold_amd.py on a synthetic New York day whose every level is known.

LONG day, minutes after 09:30 New York: the range is 99.98-101.02; at 10:20 price drops to 99.0 and the
10:15-10:30 bar closes back at 100.6 (a sweep of the lows); 10:45-11:00 opens above the sweep bar's high
(a bullish gap, 100.62 to 102.48); price returns to 101.8 by 11:20, makes a 5m swing high near 102.72
and the 11:40-11:45 bar closes above it at 103.2: the entry is due at 11:45, stop under the 101.78
pullback low. The SHORT day is the same path mirrored (200 - price).
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pytest

import strategy.gold_amd as gold_amd
from core.models import Bar, SignalDirection
from strategy.gold_amd import (
    Frame,
    GoldAmdConfig,
    GoldAmdStrategy,
    bias_series,
    build_frame,
    build_series,
    find_entry,
    session_times,
)

NY = ZoneInfo("America/New_York")
DAY = date(2026, 3, 11)
T0 = int(datetime.combine(DAY, time(9, 30), tzinfo=NY).timestamp())
ENTRY_MINUTE = 135                      # 11:45
LONG_PATH = [(-600, 100.5), (0, 100.5), (10, 101.0), (20, 100.0), (29, 100.5), (30, 100.5), (45, 100.4),
             (50, 99.0), (60, 100.6), (75, 102.5), (90, 103.5), (110, 101.8), (117, 102.7), (125, 102.1),
             (135, 103.2), (180, 104.0), (390, 104.0)]
SHORT_PATH = [(m, 200 - p) for m, p in LONG_PATH]


def day_arrays(path, start=-600, end=390, wick=0.02, spread=0.1):
    """M1 arrays (ts, o, h, l, c, spread) walking `path` = (minute after 09:30, price) waypoints."""
    m = np.arange(start, end)
    xs, ys = [p[0] for p in path], [p[1] for p in path]
    o, c = np.interp(m, xs, ys), np.interp(m + 1, xs, ys)
    return (T0 + m * 60).astype(np.int64), o, np.maximum(o, c) + wick, np.minimum(o, c) - wick, c, np.full(len(m), spread)


@pytest.fixture
def constant_bias(monkeypatch):
    def apply(value: int) -> None:
        monkeypatch.setattr(gold_amd, "bias_series", lambda f4: (f4.tc, np.full(len(f4.tc), value, dtype=np.int8)))
    return apply


def scan(path, bias, constant_bias, config=None, cut_minute=None, allow_pending=False):
    constant_bias(bias)
    config = config if config is not None else GoldAmdConfig(min_fvg=0.5)
    ts, o, h, l, c, sp = day_arrays(path)
    if cut_minute is not None:
        keep = ts < T0 + cut_minute * 60
        ts, o, h, l, c, sp = ts[keep], o[keep], h[keep], l[keep], c[keep], sp[keep]
    series = build_series(ts, o, h, l, c, sp, config)
    return find_entry(series, session_times(DAY, config), config, allow_pending=allow_pending), ts


def test_bias_follows_the_last_close_through_a_confirmed_swing():
    high = np.array([10, 11, 15, 12, 11, 14, 18], dtype=float)   # swing high 15 at bar 2, confirmed at bar 4
    close = np.array([9.5, 10.5, 14.5, 11.5, 10.5, 13.5, 17.5])
    opened = np.arange(len(high), dtype=np.int64) * 14400

    def frame(h, l, c):
        return Frame(to=opened, tc=opened + 14400, o=c, h=h, l=l, c=c)

    assert list(bias_series(frame(high, high - 1, close))[1]) == [0, 0, 0, 0, 0, 0, 1]   # only 17.5 clears 15
    assert list(bias_series(frame(-(high - 1), -high, -close))[1]) == [0, 0, 0, 0, 0, 0, -1]


def test_a_bucket_that_has_not_closed_is_dropped():
    ts = np.arange(7, dtype=np.int64) * 60                       # 00:00..00:06, 5m buckets 00:00 and 00:05
    ones = np.ones(7)
    assert len(build_frame(ts, ones, ones, ones, ones, 5).tc) == 2
    assert len(build_frame(ts, ones, ones, ones, ones, 5, complete_until=300).tc) == 1


def test_a_long_sweep_zone_and_structure_shift_makes_one_entry(constant_bias):
    result, ts = scan(LONG_PATH, 1, constant_bias)
    assert result.reason == "signal"
    e = result.entry
    assert e.direction == 1
    assert e.entry_time == T0 + ENTRY_MINUTE * 60
    assert ts[e.index] == e.entry_time
    assert e.entry == pytest.approx(103.3, abs=0.05)             # the 11:45 open plus the 0.1 spread
    assert e.stop == pytest.approx(101.68, abs=0.05)             # the 101.78 pullback low minus the spread


def test_the_mirror_day_is_a_short_at_the_same_minute(constant_bias):
    result, _ = scan(SHORT_PATH, -1, constant_bias)
    assert result.reason == "signal"
    assert result.entry.direction == -1
    assert result.entry.entry_time == T0 + ENTRY_MINUTE * 60
    assert result.entry.entry == pytest.approx(96.8, abs=0.05)
    assert result.entry.stop == pytest.approx(98.42, abs=0.05)   # the mirrored low plus twice the spread


def test_a_sweep_against_the_bias_is_not_traded(constant_bias):
    assert scan(LONG_PATH, -1, constant_bias)[0].reason == "bias_against_sweep"
    assert scan(LONG_PATH, 0, constant_bias)[0].reason == "bias_against_sweep"


def test_a_gap_narrower_than_min_fvg_is_not_a_zone(constant_bias):
    narrow = [p for p in LONG_PATH if p[0] not in (75, 90)] + [(75, 100.9), (90, 101.1)]
    narrow.sort()
    assert scan(narrow, 1, constant_bias, GoldAmdConfig(min_fvg=0.5))[0].reason == "no_fvg"
    assert scan(narrow, 1, constant_bias, GoldAmdConfig(min_fvg=0.0))[0].reason != "no_fvg"


def test_nothing_is_decided_before_the_range_is_complete(constant_bias):
    assert scan(LONG_PATH, 1, constant_bias, cut_minute=10)[0].reason == "range_not_ready"


def test_no_entry_is_visible_before_it_is_due(constant_bias):
    """Cut the data at every point before the 11:45 entry: nothing may be seen. At the entry the 11:45
    M1 bar has not formed yet, and the entry is already there, with the same stop as the full day."""
    full, _ = scan(LONG_PATH, 1, constant_bias)
    for minute in range(31, ENTRY_MINUTE):
        result, _ = scan(LONG_PATH, 1, constant_bias, cut_minute=minute, allow_pending=True)
        assert result.entry is None, f"entry visible at minute {minute}"
    result, ts = scan(LONG_PATH, 1, constant_bias, cut_minute=ENTRY_MINUTE, allow_pending=True)
    assert result.entry is not None and result.entry.index == len(ts)
    assert result.entry.entry_time == full.entry.entry_time
    assert result.entry.stop == pytest.approx(full.entry.stop)
    assert scan(LONG_PATH, 1, constant_bias, cut_minute=ENTRY_MINUTE)[0].reason == "entry_bar_missing"


def bars_of(path, constant_bias, bias=1):
    constant_bias(bias)
    ts, o, h, l, c, sp = day_arrays(path)
    return [Bar(timestamp=datetime.fromtimestamp(int(t), UTC), open=float(a), high=float(b), low=float(d),
                close=float(e), volume=0.0, spread=float(s)) for t, a, b, d, e, s in zip(ts, o, h, l, c, sp)]


def at(minute: int, seconds: int = 20) -> datetime:
    return datetime.fromtimestamp(T0 + minute * 60 + seconds, UTC)


def test_the_live_wrapper_turns_a_fresh_entry_into_a_trade_setup(constant_bias):
    bars = bars_of(LONG_PATH, constant_bias)
    setup, reason, age = GoldAmdStrategy(GoldAmdConfig(min_fvg=0.5)).find_setup(bars, "XAUUSD_", at(ENTRY_MINUTE))
    assert reason == "signal" and age == 20
    assert setup.direction is SignalDirection.BUY
    assert setup.setup_id == "setup_amd_20260311_L"
    entry, stop, target = setup.entry_zone[0], setup.stop_zone[0], setup.target_zone[0]
    assert stop < entry < target
    assert target - entry == pytest.approx(2.0 * (entry - stop), abs=1e-4)


def test_the_wrapper_sees_only_closed_bars_and_waits_for_the_entry(constant_bias):
    bars = bars_of(LONG_PATH, constant_bias)
    strategy = GoldAmdStrategy(GoldAmdConfig(min_fvg=0.5))
    assert strategy.find_setup(bars, "XAUUSD_", at(ENTRY_MINUTE - 1))[:2] == (None, "waiting_for_mss")
    # at 11:45:20 the 11:45 bar is still forming: the entry is already due and is acted on
    assert strategy.find_setup(bars, "XAUUSD_", at(ENTRY_MINUTE))[1] == "signal"


def test_an_entry_older_than_the_grace_window_is_not_chased(constant_bias):
    bars = bars_of(LONG_PATH, constant_bias)
    strategy = GoldAmdStrategy(GoldAmdConfig(min_fvg=0.5))
    assert strategy.find_setup(bars, "XAUUSD_", at(ENTRY_MINUTE, 200))[1] == "signal"      # 200 s old
    setup, reason, age = strategy.find_setup(bars, "XAUUSD_", at(ENTRY_MINUTE, 300))       # 300 s old
    assert setup is None and reason == "signal_expired" and age == 300


def test_a_short_history_cannot_carry_a_bias(constant_bias):
    bars = bars_of(LONG_PATH, constant_bias)[-300:]
    assert GoldAmdStrategy().find_setup(bars, "XAUUSD_", at(ENTRY_MINUTE))[1] == "not_enough_history"
    assert GoldAmdStrategy().find_setup([], "XAUUSD_", at(ENTRY_MINUTE))[1] == "no_bars"


def test_the_session_follows_new_york_daylight_saving():
    config = GoldAmdConfig()
    winter, summer = session_times(date(2026, 1, 12), config), session_times(date(2026, 7, 13), config)
    assert datetime.fromtimestamp(winter.range_start, UTC).hour == 14      # 09:30 EST = 14:30 UTC
    assert datetime.fromtimestamp(summer.range_start, UTC).hour == 13      # 09:30 EDT = 13:30 UTC
    assert summer.flat - summer.range_start == int(timedelta(hours=6, minutes=25).total_seconds())
