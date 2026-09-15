"""Real ticks decide where a stop filled after a weekend, and are cached so a rerun is offline."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backtest.live_replay.ticks import TickCache
from core.models import SignalDirection

MONDAY_OPEN = int(datetime(2026, 9, 13, 22, 0, tzinfo=UTC).timestamp())


def test_the_stale_weekend_requote_does_not_trigger_the_stop(tmp_path: Path) -> None:
    calls: list[tuple] = []

    def fetch(symbol: str, start: int, end: int) -> list[tuple[int, float, float]]:
        calls.append((symbol, start, end))
        # The first tick of the week re-quotes Friday's close; the real price arrives 6s later.
        return [(start * 1000, 29370.20, 29373.20), (start * 1000 + 6000, 29030.33, 29033.33)]

    cache = TickCache(tmp_path, fetch)
    assert cache.first_crossing("NDX100", MONDAY_OPEN, SignalDirection.BUY, 29326.58) == 29030.33
    assert cache.first_crossing("NDX100", MONDAY_OPEN, SignalDirection.BUY, 29326.58) == 29030.33
    assert len(calls) == 1, "the second call must be served from the cache"


def test_a_short_stop_crosses_on_the_ask(tmp_path: Path) -> None:
    cache = TickCache(tmp_path, lambda s, a, b: [(a * 1000, 100.0, 101.0), (a * 1000 + 1, 100.5, 102.5)])
    assert cache.first_crossing("GER40", MONDAY_OPEN, SignalDirection.SELL, 102.0) == 102.5


def test_a_window_with_no_crossing_tick_returns_nothing(tmp_path: Path) -> None:
    cache = TickCache(tmp_path, lambda s, a, b: [(a * 1000, 100.0, 101.0)])
    assert cache.first_crossing("GER40", MONDAY_OPEN, SignalDirection.BUY, 50.0) is None


def test_nothing_is_fetched_before_the_brokers_tick_history_starts(tmp_path: Path) -> None:
    cache = TickCache(tmp_path, lambda *a: pytest.fail("must not fetch"))
    before = int(datetime(2024, 1, 8, tzinfo=UTC).timestamp())
    assert cache.first_crossing("NDX100", before, SignalDirection.BUY, 1.0) is None


def test_gold_ticks_start_later_than_the_indices(tmp_path: Path) -> None:
    cache = TickCache(tmp_path, lambda *a: pytest.fail("must not fetch"))
    march_2026 = int(datetime(2026, 3, 2, tzinfo=UTC).timestamp())
    assert cache.has_history("NDX100", march_2026) is True
    assert cache.has_history("XAUUSD", march_2026) is False


def test_an_empty_window_is_cached_too(tmp_path: Path) -> None:
    calls: list[int] = []

    def fetch(symbol: str, start: int, end: int) -> list:
        calls.append(start)
        return []

    cache = TickCache(tmp_path, fetch)
    assert cache.window("NDX100", MONDAY_OPEN, 60) == []
    assert cache.window("NDX100", MONDAY_OPEN, 60) == []
    assert len(calls) == 1
