"""Unit tests for strategy/session_utils.py's TIMEFRAME_MINUTES mapping and
session_length_in_bars helper, used by the session-scoped strategies'
recommended_max_holding_bars() overrides.
"""

from datetime import time

import pytest

from core.models import Timeframe
from strategy.session_utils import TIMEFRAME_MINUTES, session_length_in_bars


class TestTimeframeMinutes:
    def test_covers_every_timeframe_member(self) -> None:
        assert set(TIMEFRAME_MINUTES.keys()) == set(Timeframe)

    @pytest.mark.parametrize(
        ("timeframe", "minutes"),
        [
            (Timeframe.M1, 1),
            (Timeframe.M5, 5),
            (Timeframe.M15, 15),
            (Timeframe.M30, 30),
            (Timeframe.H1, 60),
            (Timeframe.H4, 240),
            (Timeframe.D1, 1440),
        ],
    )
    def test_maps_to_expected_minute_count(self, timeframe: Timeframe, minutes: int) -> None:
        assert TIMEFRAME_MINUTES[timeframe] == minutes


class TestSessionLengthInBars:
    def test_accumulation_breakout_default_session_at_m1(self) -> None:
        assert session_length_in_bars(time(9, 30), time(11, 0), Timeframe.M1) == 90

    def test_classic_cash_session_at_m5(self) -> None:
        assert session_length_in_bars(time(9, 30), time(16, 0), Timeframe.M5) == 78

    def test_classic_cash_session_at_m1(self) -> None:
        assert session_length_in_bars(time(9, 30), time(16, 0), Timeframe.M1) == 390

    def test_near_continuous_cfd_session_at_m5(self) -> None:
        assert session_length_in_bars(time(9, 30), time(23, 0), Timeframe.M5) == 162

    def test_span_not_evenly_divisible_floors_to_whole_bars(self) -> None:
        # 09:30-09:47 = 17 minutes; at M5, 17 // 5 = 3 whole bars.
        assert session_length_in_bars(time(9, 30), time(9, 47), Timeframe.M5) == 3

    def test_end_equal_to_start_raises(self) -> None:
        with pytest.raises(ValueError, match="must be later than"):
            session_length_in_bars(time(9, 30), time(9, 30), Timeframe.M1)

    def test_end_before_start_raises(self) -> None:
        with pytest.raises(ValueError, match="must be later than"):
            session_length_in_bars(time(11, 0), time(9, 30), Timeframe.M1)
