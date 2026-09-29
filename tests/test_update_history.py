"""data/update_history.update_symbol: fetches to the broker's real "now", keeps only closed bars.

MT5 reads request bounds as the broker's wall clock, which runs ahead of UTC (3 hours on CFI in
summer), so asking up to real UTC "now" stopped every update 3 hours short (2026-09-29).
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import data.update_history as update_history
from core.models import Bar
from mt5.rates import BROKER_TZ


def _bar(ts: datetime) -> Bar:
    return Bar(timestamp=ts, open=100.0, high=101.0, low=99.0, close=100.5, volume=10.0, spread=0.1)


def test_fetches_past_utc_now_and_drops_the_bar_still_forming(tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    existing = now - timedelta(minutes=10)
    path = tmp_path / "TEST_M1.csv"
    path.write_text("time,open,high,low,close,volume,spread\n"
                    f"{existing.astimezone(BROKER_TZ):%Y-%m-%d %H:%M:%S},100,101,99,100.5,10,0.1\n",
                    encoding="utf-8")
    asked = {}

    def fake_fetch(symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Bar]:
        asked["end"] = end
        # the last closed minute, and the minute that has just opened (still forming)
        return [_bar(now - timedelta(minutes=1)), _bar(now)]

    monkeypatch.setattr(update_history, "fetch_symbol_bars_chunked", fake_fetch)
    update_history.update_symbol("TEST", "M1", overlap_days=5, output_dir=tmp_path)

    assert asked["end"] > now + timedelta(hours=12)  # past the broker's clock, not UTC's
    written = update_history.load_existing_bars(path)
    assert [b.timestamp for b in written] == [existing, now - timedelta(minutes=1)]
