"""Bars the replay trades on: server-clock CSVs in, UTC bars and MT5-style M15 out."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backtest.live_replay.market import aggregate, load_fx, load_m1


def _csv(tmp_path: Path, name: str, rows: list[tuple[str, float, float, float, float, float]]) -> Path:
    path = tmp_path / name
    lines = ["time,open,high,low,close,volume,spread"]
    lines += [f"{t},{o},{h},{low},{c},1.0,{s}" for t, o, h, low, c, s in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


def test_summer_server_time_is_three_hours_ahead_of_utc(tmp_path: Path) -> None:
    data = _csv(tmp_path, "TEST_M1.csv", [("2026-09-14 17:10:00", 100.0, 101.0, 99.0, 100.5, 3.0)])
    bar = load_m1("TEST", data).bar(0)
    assert bar.timestamp == datetime(2026, 9, 14, 14, 10, tzinfo=UTC)
    assert bar.spread == 3.0


def test_winter_server_time_is_two_hours_ahead_of_utc(tmp_path: Path) -> None:
    data = _csv(tmp_path, "TEST_M1.csv", [("2026-01-15 16:30:00", 1.0, 1.0, 1.0, 1.0, 0.0)])
    assert load_m1("TEST", data).bar(0).timestamp == datetime(2026, 1, 15, 14, 30, tzinfo=UTC)


def test_a_bar_is_visible_only_once_it_has_closed(tmp_path: Path) -> None:
    data = _csv(tmp_path, "TEST_M1.csv", [("2026-09-14 17:10:00", 1.0, 1.0, 1.0, 1.0, 0.0),
                                          ("2026-09-14 17:11:00", 1.0, 1.0, 1.0, 1.0, 0.0)])
    frame = load_m1("TEST", data)
    opened = int(datetime(2026, 9, 14, 14, 11, tzinfo=UTC).timestamp())
    assert frame.count_closed_by(opened) == 1
    assert frame.count_closed_by(opened + 59) == 1
    assert frame.count_closed_by(opened + 60) == 2


def test_m15_bars_are_labelled_by_their_open_and_aggregate_ohlc(tmp_path: Path) -> None:
    rows = [(f"2026-09-14 16:{m:02d}:00", 100.0 + m, 101.0 + m, 99.0 + m, 100.5 + m, 1.0)
            for m in range(30, 48)]
    frame = aggregate(load_m1("TEST", _csv(tmp_path, "TEST_M1.csv", rows)), 15)
    first = frame.bar(0)
    assert len(frame) == 2
    assert first.timestamp == datetime(2026, 9, 14, 13, 30, tzinfo=UTC)
    assert (first.open, first.high, first.low, first.close) == (130.0, 145.0, 129.0, 144.5)
    assert frame.ts_close[0] - frame.ts[0] == 900
    assert (frame.bar(1).open, frame.bar(1).close) == (145.0, 147.5)


def test_a_date_window_trims_the_frame(tmp_path: Path) -> None:
    rows = [("2026-09-13 17:10:00", 1.0, 1.0, 1.0, 1.0, 0.0),
            ("2026-09-14 17:10:00", 2.0, 2.0, 2.0, 2.0, 0.0)]
    frame = load_m1("TEST", _csv(tmp_path, "TEST_M1.csv", rows),
                    start=datetime(2026, 9, 14, tzinfo=UTC))
    assert len(frame) == 1 and frame.bar(0).open == 2.0


def test_fx_uses_the_previous_days_close_so_nothing_looks_ahead(tmp_path: Path) -> None:
    _csv(tmp_path, "USDJPY_D1.csv", [("2026-09-10 00:00:00", 150.0, 150.0, 150.0, 150.0, 0.0),
                                     ("2026-09-11 00:00:00", 155.0, 155.0, 155.0, 155.0, 0.0)])
    fx = load_fx("JPY", tmp_path)
    during_the_eleventh = int(datetime(2026, 9, 11, 12, tzinfo=UTC).timestamp())
    assert fx.usd_per_unit(during_the_eleventh) == pytest.approx(1 / 150)


def test_dollar_symbols_need_no_fx_file(tmp_path: Path) -> None:
    assert load_fx("USD", tmp_path).usd_per_unit(0) == 1.0
