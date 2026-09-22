"""Unit tests for mt5/rates.py's timezone handling.

Covers two related fixes:
- BROKER_TZ is the clock of the broker .env names (config/brokers.py
  SERVER_CLOCKS -- New York close for both), overridable via the
  MT5_BROKER_TZ env var. It was hardcoded to "Europe/Bucharest" until
  2026-09-22, which both brokers turned out not to run.
- rates_to_bars() disambiguates the one ambiguous local hour per year during
  BROKER_TZ's autumn DST fall-back, instead of always picking Python's
  fold=0 default (which silently produces a backwards timestamp jump for the
  second, real occurrence of that hour).
"""

import importlib
from datetime import UTC, datetime, timedelta

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
    @staticmethod
    def _reloaded(monkeypatch: pytest.MonkeyPatch, **env: str):
        """mt5.rates re-imported under `env` (MT5_BROKER_TZ cleared unless given)."""
        monkeypatch.delenv("MT5_BROKER_TZ", raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        import mt5.rates as rates_module

        return importlib.reload(rates_module)

    @pytest.fixture(autouse=True)
    def _restore(self, monkeypatch: pytest.MonkeyPatch):
        yield
        monkeypatch.undo()
        import mt5.rates as rates_module

        importlib.reload(rates_module)

    @pytest.mark.parametrize("server", ["CFI11-Demo", "FundingPips-Trial"])
    def test_defaults_to_the_brokers_own_clock(self, monkeypatch: pytest.MonkeyPatch, server: str) -> None:
        """Both brokers' servers were measured to run New York close (config/brokers.py)."""
        rates_module = self._reloaded(monkeypatch, MT5_SERVER=server)
        assert str(rates_module.BROKER_TZ) == "America/New_York+7"

    def test_no_broker_at_all_falls_back_to_new_york_close(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rates_module = self._reloaded(monkeypatch, MT5_SERVER="")
        assert str(rates_module.BROKER_TZ) == "America/New_York+7"

    @pytest.mark.parametrize("value", ["America/New_York", "America/New_York+7", "Europe/Bucharest"])
    def test_env_var_overrides_the_default(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        rates_module = self._reloaded(monkeypatch, MT5_SERVER="CFI11-Demo", MT5_BROKER_TZ=value)
        assert str(rates_module.BROKER_TZ) == value


class TestRatesToBarsDstFallBack:
    """A server clock's autumn fall-back reports one wall-clock hour TWICE in one real H1 bar
    sequence. New York close (both brokers) falls back with New York on 2026-11-01, from 09:00
    UTC+3 to 08:00 UTC+2, so 08:00-08:59 repeats; Europe/Bucharest did the same with 03:00-03:59
    on 2026-10-25."""

    @pytest.mark.parametrize(("clock", "day", "hours"), [
        ("America/New_York+7", (2026, 11, 1), (7, 8, 8, 9)),
        ("Europe/Bucharest", (2026, 10, 25), (2, 3, 3, 4)),
    ])
    def test_repeated_hour_resolves_to_strictly_increasing_utc_timestamps(
        self, monkeypatch: pytest.MonkeyPatch, clock: str, day: tuple[int, int, int],
        hours: tuple[int, ...],
    ) -> None:
        import mt5.rates as rates_module
        from core.broker_clock import BrokerClock

        monkeypatch.setattr(rates_module, "BROKER_TZ", BrokerClock.parse(clock))
        # unambiguous summer, ambiguous 1st (summer), ambiguous 2nd (winter), unambiguous winter
        rows = [(_epoch_for_naive_wallclock(*day, hour, 30), 1, 1, 1, 1, 100, 0) for hour in hours]

        bars = rates_module.rates_to_bars(_fake_rates(rows), point=0.01)

        timestamps = [b.timestamp for b in bars]
        assert timestamps == sorted(timestamps)
        assert len(set(timestamps)) == 4  # no two bars silently collapsed to the same instant
        # Exactly one hour apart at every step, including across the repeated
        # local hour -- the real-world cadence of consecutive H1 bars.
        deltas = [(timestamps[i + 1] - timestamps[i]).total_seconds() for i in range(3)]
        assert deltas == [3600.0, 3600.0, 3600.0]
        # the first bar is on the summer offset, UTC+3
        assert timestamps[0] == datetime(*day, hours[0], 30, tzinfo=UTC) - timedelta(hours=3)

    def test_unambiguous_times_are_unaffected(self) -> None:
        from mt5.rates import rates_to_bars

        rows = [
            (_epoch_for_naive_wallclock(2026, 1, 15, 12, 0), 1, 1, 1, 1, 100, 0),
            (_epoch_for_naive_wallclock(2026, 1, 15, 13, 0), 1, 1, 1, 1, 100, 0),
        ]

        bars = rates_to_bars(_fake_rates(rows), point=0.01)

        assert (bars[1].timestamp - bars[0].timestamp).total_seconds() == 3600.0
