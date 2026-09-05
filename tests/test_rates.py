"""Unit tests for mt5/rates.py's timezone handling.

Covers two related fixes:
- BROKER_TZ can be overridden via the MT5_BROKER_TZ env var (it was
  previously hardcoded to "Europe/Bucharest", verified only for
  ForexTimeFXTM-Demo02 -- see BROKER_TZ's module-level docstring).
- rates_to_bars() disambiguates the one ambiguous local hour per year during
  BROKER_TZ's autumn DST fall-back, instead of always picking Python's
  fold=0 default (which silently produces a backwards timestamp jump for the
  second, real occurrence of that hour).
"""

import importlib
from datetime import UTC, datetime

import numpy as np
import pytest


def _fake_rates(rows: list[tuple[int, float, float, float, float, int, int]]) -> np.ndarray:
    return np.array(
        [(*row, 0) for row in rows],
        dtype=[
            ("time", "i8"),
            ("open", "f8"),
            ("high", "f8"),
            ("low", "f8"),
            ("close", "f8"),
            ("tick_volume", "i8"),
            ("spread", "i4"),
            ("real_volume", "i8"),
        ],
    )


def _epoch_for_naive_wallclock(year: int, month: int, day: int, hour: int, minute: int) -> int:
    """MT5's raw epoch numerically equals the broker's local wall-clock when
    (mis)read as UTC -- see BROKER_TZ's docstring. This reproduces that
    encoding for a given broker-local wall-clock reading."""
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp())


class TestBrokerTzOverride:
    def test_defaults_to_europe_bucharest_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MT5_BROKER_TZ", raising=False)
        import mt5.rates as rates_module

        importlib.reload(rates_module)
        try:
            assert str(rates_module.BROKER_TZ) == "Europe/Bucharest"
        finally:
            monkeypatch.delenv("MT5_BROKER_TZ", raising=False)
            importlib.reload(rates_module)

    def test_env_var_overrides_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MT5_BROKER_TZ", "America/New_York")
        import mt5.rates as rates_module

        importlib.reload(rates_module)
        try:
            assert str(rates_module.BROKER_TZ) == "America/New_York"
        finally:
            monkeypatch.delenv("MT5_BROKER_TZ", raising=False)
            importlib.reload(rates_module)


class TestRatesToBarsDstFallBack:
    """Europe/Bucharest exits DST on 2026-10-25: local clocks go from 04:00
    EEST (UTC+3) back to 03:00 EET (UTC+2), so the 03:00-03:59 wall-clock
    hour is reported by MT5 TWICE in one real H1 bar sequence."""

    def test_repeated_hour_resolves_to_strictly_increasing_utc_timestamps(self) -> None:
        from mt5.rates import rates_to_bars

        rows = [
            (_epoch_for_naive_wallclock(2026, 10, 25, 2, 30), 1, 1, 1, 1, 100, 0),  # unambiguous EEST
            (_epoch_for_naive_wallclock(2026, 10, 25, 3, 30), 1, 1, 1, 1, 100, 0),  # ambiguous, 1st (EEST)
            (_epoch_for_naive_wallclock(2026, 10, 25, 3, 30), 1, 1, 1, 1, 100, 0),  # ambiguous, 2nd (EET)
            (_epoch_for_naive_wallclock(2026, 10, 25, 4, 30), 1, 1, 1, 1, 100, 0),  # unambiguous EET
        ]

        bars = rates_to_bars(_fake_rates(rows), point=0.01)

        timestamps = [b.timestamp for b in bars]
        assert timestamps == sorted(timestamps)
        assert len(set(timestamps)) == 4  # no two bars silently collapsed to the same instant
        # Exactly one hour apart at every step, including across the repeated
        # local hour -- the real-world cadence of consecutive H1 bars.
        deltas = [(timestamps[i + 1] - timestamps[i]).total_seconds() for i in range(3)]
        assert deltas == [3600.0, 3600.0, 3600.0]

    def test_unambiguous_times_are_unaffected(self) -> None:
        from mt5.rates import rates_to_bars

        rows = [
            (_epoch_for_naive_wallclock(2026, 1, 15, 12, 0), 1, 1, 1, 1, 100, 0),
            (_epoch_for_naive_wallclock(2026, 1, 15, 13, 0), 1, 1, 1, 1, 100, 0),
        ]

        bars = rates_to_bars(_fake_rates(rows), point=0.01)

        assert (bars[1].timestamp - bars[0].timestamp).total_seconds() == 3600.0
