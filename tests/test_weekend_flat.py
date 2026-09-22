"""--weekend-flat: be flat before the broker shuts gold for the weekend, and open nothing after.

Why the rule exists (live replay spike, 2026-09-16): XAUUSD Breakout 60m/3R closed on Friday's
last bar instead of holding over the weekend went from PF 1.18 to 1.26 (t 1.88 -> 2.89), and
was better in 5 of 7 years. It runs as a separate Paper bot first, so the plain 60m/3R bot
keeps its own record for the A/B.

Why the cutoff is in broker server time: FundingPips closes gold at 23:48 server time every
Friday (2024-09..2026-09), which is 16:48 New York most weeks but 17:48 in the weeks when only
one side of the Atlantic has changed its clocks.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import run_live_nasdaq_orb as runner
from core.models import Bar, OrderType, Timeframe
from execution.models import Position
from execution.trade_manager import TradeManager
from strategy.nasdaq_orb_m1_breakout import NasdaqOrbM1BreakoutConfig, NasdaqOrbM1BreakoutStrategy


def _server(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=runner.BROKER_TZ)


class TestWeekendFlatDue:
    def test_friday_just_before_the_cutoff_is_not_due(self) -> None:
        assert not runner.weekend_flat_due(_server(2026, 9, 11, 23, 39))

    def test_friday_at_the_cutoff_is_due(self) -> None:
        assert runner.weekend_flat_due(_server(2026, 9, 11, 23, 40))

    def test_thursday_night_is_not_due(self) -> None:
        assert not runner.weekend_flat_due(_server(2026, 9, 10, 23, 50))

    def test_the_whole_weekend_is_due(self) -> None:
        assert runner.weekend_flat_due(_server(2026, 9, 12, 12, 0))
        assert runner.weekend_flat_due(_server(2026, 9, 13, 23, 59))

    def test_monday_after_the_open_is_not_due(self) -> None:
        assert not runner.weekend_flat_due(_server(2026, 9, 14, 1, 5))

    def test_winter_cutoff_is_in_server_time(self) -> None:
        # 2026-01-16: EET (UTC+2) -- 23:40 server is 21:40 UTC, 16:40 New York.
        assert runner.weekend_flat_due(datetime(2026, 1, 16, 21, 40, tzinfo=UTC))
        assert not runner.weekend_flat_due(datetime(2026, 1, 16, 21, 39, tzinfo=UTC))

    def test_a_week_when_only_new_york_has_changed_clocks_still_uses_server_time(self) -> None:
        # 2026-03-20: the US is on summer time, Europe is not -- 23:40 server is 17:40 New York.
        assert runner.weekend_flat_due(datetime(2026, 3, 20, 21, 40, tzinfo=UTC))
        assert not runner.weekend_flat_due(datetime(2026, 3, 20, 21, 39, tzinfo=UTC))


class TestVariantFiles:
    def test_without_a_variant_the_existing_file_names_are_unchanged(self) -> None:
        assert runner._bot_tag("XAUUSD", None) == "xauusd"

    def test_a_variant_gets_files_of_its_own(self) -> None:
        assert runner._bot_tag("XAUUSD", "weekendflat") == "xauusd_weekendflat"

    def test_the_flags_parse(self) -> None:
        args = runner.parse_args(["--symbol", "XAUUSD", "--tp-r", "3.0", "--paper",
                                  "--weekend-flat", "--variant", "weekendflat"])
        assert args.weekend_flat is True and args.variant == "weekendflat"

    def test_both_flags_are_off_by_default(self) -> None:
        args = runner.parse_args(["--symbol", "XAUUSD", "--tp-r", "3.0"])
        assert args.weekend_flat is False and args.variant is None

    @pytest.mark.parametrize("bad", ["../x", "Weekend", "week end", ""])
    def test_a_variant_that_is_unsafe_in_a_file_name_is_refused(self, bad: str) -> None:
        with pytest.raises(SystemExit):
            runner.parse_args(["--symbol", "XAUUSD", "--tp-r", "3.0", "--variant", bad])


class _Connector:
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    def fetch_recent_bars(self, symbol: str, timeframe: str, count: int) -> list[Bar]:
        return self._bars


class _PaperBroker:
    def __init__(self, positions: list[Position]) -> None:
        self.positions = positions
        self.closed: list[str] = []

    def get_open_positions(self) -> list[Position]:
        return list(self.positions)

    def close_position(self, position_id: str):
        self.closed.append(position_id)
        self.positions = [p for p in self.positions if p.id != position_id]
        return SimpleNamespace(success=True, retcode=0, comment="closed")

    def get_pending_orders(self, symbol: str) -> list:
        return []  # every poll now looks for a leftover reverse order (cancel_reverse_orders)


def _gold_position() -> Position:
    return Position(id="p1", symbol="XAUUSD", order_type=OrderType.BUY_MARKET, volume=0.02,
                    open_price=4285.60, current_price=4290.00, stop_loss=4264.09, take_profit=4349.77,
                    timestamp=datetime(2026, 9, 11, 15, 8, tzinfo=UTC),
                    comment="setup_nasdaq_orb_m1_XAUUSD_M1_BUY_20260911_150600")


def _quiet_bars(until: datetime) -> list[Bar]:
    """Bars that touch neither the stop nor the target, up to `until`."""
    return [Bar(timestamp=until - timedelta(minutes=i), open=4290.0, high=4291.0, low=4289.0,
                close=4290.0, volume=10.0) for i in range(5, 0, -1)]


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(runner, "_log_trade_event", lambda event, **_fields: seen.append(event))
    return seen


def _run(broker: _PaperBroker, now: datetime, *, weekend_flat: bool, tmp_path) -> None:
    runner.run_once(
        connector=_Connector(_quiet_bars(now)), broker=broker, trade_manager=TradeManager(volume=0.01),
        strategy=NasdaqOrbM1BreakoutStrategy(config=NasdaqOrbM1BreakoutConfig(or_minutes=60, tp_r=3.0)),
        symbol="XAUUSD", timeframe=Timeframe.M1, timeframe_str="M1", lookback_days=2,
        kill_switch_flag_path=tmp_path / "no_kill_switch.flag",
        traded_setups_path=tmp_path / "traded_setups.json",
        weekend_flat=weekend_flat, now=now,
    )


class TestRunOnce:
    def test_an_open_position_is_closed_at_the_friday_cutoff(self, events, tmp_path) -> None:
        broker = _PaperBroker([_gold_position()])
        _run(broker, _server(2026, 9, 11, 23, 40), weekend_flat=True, tmp_path=tmp_path)
        assert broker.closed == ["p1"]
        assert "closed_weekend_flat" in events

    def test_before_the_cutoff_the_position_is_managed_as_usual(self, events, tmp_path) -> None:
        broker = _PaperBroker([_gold_position()])
        _run(broker, _server(2026, 9, 11, 23, 38), weekend_flat=True, tmp_path=tmp_path)
        assert broker.closed == []
        assert "held" in events

    def test_without_the_flag_nothing_is_closed_for_the_weekend(self, events, tmp_path) -> None:
        broker = _PaperBroker([_gold_position()])
        _run(broker, _server(2026, 9, 11, 23, 46), weekend_flat=False, tmp_path=tmp_path)
        assert broker.closed == []

    def test_no_new_entry_is_looked_for_after_the_cutoff(self, events, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(runner, "_evaluate_for_new_trade",
                            lambda *a, **k: pytest.fail("must not look for an entry after the cutoff"))
        _run(_PaperBroker([]), _server(2026, 9, 11, 23, 42), weekend_flat=True, tmp_path=tmp_path)
        assert "entry_blocked_weekend_flat" in events

    def test_entries_are_looked_for_before_the_cutoff(self, events, tmp_path, monkeypatch) -> None:
        calls: list[int] = []
        monkeypatch.setattr(runner, "_evaluate_for_new_trade", lambda *a, **k: calls.append(1))
        _run(_PaperBroker([]), _server(2026, 9, 11, 23, 0), weekend_flat=True, tmp_path=tmp_path)
        assert calls == [1]
