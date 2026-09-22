"""Tests for scripts/kill_rule.py -- the live bot's pre-registered stop rule.

The adopted thresholds are pinned first. A pre-registered rule is only worth anything if it is not
loosened after live trades arrive, so changing them has to break a test rather than slip through a
JSON edit.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import MetaTrader5 as mt5
import pytest

from backtest.live_replay.configs import load_roster
from execution.stop_and_reverse import reverse_comment
from scripts import kill_rule as kr

PREFIX = "setup_nasdaq_orb_m1"
T0 = datetime(2026, 9, 22, 14, 0, tzinfo=UTC)
RULE = kr.KillRule(task="T", adopted=datetime(2026, 9, 21, 6, 59, 21, tzinfo=UTC),
                   stops=(kr.StopClause(40, 17.0), kr.StopClause(80, 22.0)),
                   checkpoint_at=40, checkpoint_min_net_r=-5.8)
# The expected offsets below are the server's September UTC+3: New York close on both brokers, and
# also what an MT5_BROKER_TZ override to Europe/Bucharest reads in September.
SERVER_ON_UTC_PLUS_3 = datetime(2026, 9, 21, tzinfo=UTC).astimezone(kr.BROKER_TZ).utcoffset() == timedelta(hours=3)


def _trades(rs: list[float]) -> list[kr.LiveTrade]:
    """Trades whose risk is 1, so each one's R is its net."""
    return [kr.LiveTrade(position=i, opened=T0 + timedelta(hours=i), long=True, net=r, risk=1.0)
            for i, r in enumerate(rs)]


class TestAdoptedRuleIsPinned:
    def test_the_live_bots_numbers_are_the_ones_fixed_before_its_first_trade(self) -> None:
        rule = kr.load_rules("cfi")["OrbBreakoutwf_XAUUSD_Demo"]

        assert rule.adopted == datetime(2026, 9, 21, 6, 59, 21, tzinfo=UTC)   # commit 0abd3a5
        assert rule.stops == (kr.StopClause(40, 17.0), kr.StopClause(80, 22.0))
        assert (rule.checkpoint_at, rule.checkpoint_min_net_r) == (40, -5.8)
        assert rule.horizon == 80

    def test_the_fvg_bots_numbers_are_the_ones_fixed_before_its_first_trade(self) -> None:
        # scripts/fvg_window_envelope.py, full CFI history with swap, seed 20260922. Adopted at
        # 25.1/35.4/-14.3 on Bucharest-read bars; revised the same day, before any live trade, to
        # the same script on the broker's real clock -- tighter on every clause, never looser.
        rule = kr.load_rules("cfi")["FvgWindow_NDX100_Demo"]

        assert rule.adopted == datetime(2026, 9, 22, 6, 22, 57, tzinfo=UTC)
        assert rule.stops == (kr.StopClause(40, 24.8), kr.StopClause(80, 34.7))
        assert (rule.checkpoint_at, rule.checkpoint_min_net_r) == (40, -14.1)

    @pytest.mark.parametrize(("task", "dd40", "dd80", "net40"), [
        ("OrbBreakoutinv_DJI30_Demo", 10.6, 15.7, -7.1),
        ("OrbBreakoutinv_SPX500_Demo", 10.3, 15.0, -6.8),
        ("OrbSweep_GER40_Demo", 12.0, 14.6, -1.4),
        ("OrbSweep_JP225_Demo", 26.9, 40.8, -19.0),
    ])
    def test_the_four_bots_added_on_every_free_symbol_are_pinned_too(
        self, task: str, dd40: float, dd80: float, net40: float
    ) -> None:
        # Full CFI history (2025-01..2026-09), the same bootstrap as the FVG bot, fixed before deployment.
        rule = kr.load_rules("cfi")[task]

        assert rule.adopted == datetime(2026, 9, 22, 11, 47, 35, tzinfo=UTC)
        assert rule.stops == (kr.StopClause(40, dd40), kr.StopClause(80, dd80))
        assert (rule.checkpoint_at, rule.checkpoint_min_net_r) == (40, net40)

    @pytest.mark.parametrize(("task", "dd40", "dd80", "net40"), [
        ("OrbBreakoutwf_XAUUSD_Demo", 17.6, 22.6, -6.1),
        ("FvgWindow_NDX100_Demo", 26.3, 37.9, -15.6),
        ("OrbBreakoutinv_DJI30_Demo", 11.4, 17.2, -7.8),
        ("OrbBreakoutinv_SPX500_Demo", 12.0, 18.2, -8.6),
        ("OrbSweep_GER40_Demo", 19.6, 27.8, -11.8),
        ("OrbSweep_JP225_Demo", 25.3, 38.3, -17.6),
    ])
    def test_the_fundingpips_rules_are_pinned_and_are_not_cfis(
        self, task: str, dd40: float, dd80: float, net40: float
    ) -> None:
        # The same tasks on FundingPips' own prices, fixed before that account's first order.
        rule = kr.load_rules("fundingpips")[task]

        assert rule.adopted == datetime(2026, 9, 22, 13, 52, 21, tzinfo=UTC)
        assert rule.stops == (kr.StopClause(40, dd40), kr.StopClause(80, dd80))
        assert (rule.checkpoint_at, rule.checkpoint_min_net_r) == (40, net40)
        assert rule != kr.load_rules("cfi")[task]

    @pytest.mark.parametrize("broker", ["cfi", "fundingpips"])
    def test_every_rule_names_a_bot_the_roster_deploys_on_that_account(self, broker: str) -> None:
        deployed = {task for task, brokers in load_roster().items() if broker in brokers}
        assert set(kr.load_rules(broker)) <= deployed

    @pytest.mark.parametrize("broker", ["cfi", "fundingpips"])
    def test_every_bot_the_roster_deploys_has_a_rule_on_that_account(self, broker: str) -> None:
        """A Demo bot with no pre-registered stop is a bot nothing will ever take out."""
        deployed = {task for task, brokers in load_roster().items() if broker in brokers}
        assert deployed <= set(kr.load_rules(broker))

    def test_a_missing_file_means_no_rules_rather_than_a_crash(self, tmp_path) -> None:
        assert kr.load_rules("cfi", tmp_path / "nope.json") == {}

    def test_an_adoption_time_without_an_offset_is_refused(self, tmp_path) -> None:
        # A naive time would be read as local time on whatever machine runs the report.
        path = tmp_path / "rules.json"
        path.write_text('{"cfi": {"T": {"adopted": "2026-09-21T06:59:21", "stops": [], '
                        '"checkpoint": {"at_trades": 40, "min_net_r": -5.8}}}}', encoding="utf-8")

        with pytest.raises(ValueError):
            kr.load_rules("cfi", path)


class TestMt5Constants:
    def test_the_repeated_values_still_match_metatrader5(self) -> None:
        assert (kr.DEAL_ENTRY_IN, kr.DEAL_ENTRY_OUT, kr.DEAL_ENTRY_OUT_BY) == (
            mt5.DEAL_ENTRY_IN, mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)
        assert kr.DEAL_TYPE_BUY == mt5.DEAL_TYPE_BUY


@pytest.mark.skipif(not SERVER_ON_UTC_PLUS_3, reason="the expected offsets are the server's September UTC+3")
class TestDealTime:
    def test_a_summer_broker_stamp_is_three_hours_ahead_of_utc(self) -> None:
        stamp = datetime(2026, 9, 22, 16, 30, tzinfo=UTC)   # how MT5 encodes 16:30 server time

        assert kr.deal_time_utc(int(stamp.timestamp())) == datetime(2026, 9, 22, 13, 30, tzinfo=UTC)


def _deal(position: int, entry: int, *, time: datetime = T0, symbol: str = "XAUUSD_",
          comment: str = PREFIX + "_XAUUSD_M1_BUY", type_: int = 0, order: int = 10,
          volume: float = 0.1, price: float = 3700.0, profit: float = 0.0, swap: float = 0.0,
          commission: float = 0.0, fee: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(position_id=position, entry=entry, time=int(time.timestamp()),
                           symbol=symbol, comment=comment, type=type_, order=order, volume=volume,
                           price=price, profit=profit, swap=swap, commission=commission, fee=fee)


STOPS = {10: 3690.0, 20: 3710.0}   # opening order ticket -> the stop it carried


def _stop_of(ticket: int) -> float | None:
    return STOPS.get(ticket)


def _risk_of(long: bool, volume: float, entry: float, stop: float) -> float:
    """A gold contract of 100 oz, signed like order_calc_profit: a loss is negative."""
    return (stop - entry) * volume * 100 * (1 if long else -1)


def _as_utc(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, UTC)


def _pair(deals: list, **kw) -> list[kr.LiveTrade]:
    args = dict(symbol="XAUUSD_", prefix=PREFIX, since=RULE.adopted, stop_of=_stop_of,
                risk_of=_risk_of, to_utc=_as_utc)
    args.update(kw)
    return kr.pair_trades(deals, **args)


class TestPairTrades:
    def test_net_is_every_deal_and_risk_is_the_brokers_loss_at_the_stop(self) -> None:
        # Long 0.1 lot at 3700, stop 3690: risk 10 * 0.1 * 100 = 100. Net 250 - 5 - 1 - 2 = 242.
        deals = [_deal(1, kr.DEAL_ENTRY_IN, commission=-2.0),
                 _deal(1, kr.DEAL_ENTRY_OUT, time=T0 + timedelta(hours=1),
                       profit=250.0, swap=-5.0, fee=-1.0)]

        (trade,) = _pair(deals)

        assert trade.long
        assert (trade.net, trade.risk) == (pytest.approx(242.0), pytest.approx(100.0))
        assert trade.r == pytest.approx(2.42)

    def test_a_short_is_measured_against_its_stop_above(self) -> None:
        deals = [_deal(2, kr.DEAL_ENTRY_IN, type_=1, order=20),
                 _deal(2, kr.DEAL_ENTRY_OUT, time=T0 + timedelta(hours=1), profit=-100.0)]

        (trade,) = _pair(deals)

        assert not trade.long
        assert trade.r == pytest.approx(-1.0)

    def test_only_this_bots_closed_positions_since_adoption_count(self) -> None:
        later = T0 + timedelta(hours=1)
        deals = [
            _deal(1, kr.DEAL_ENTRY_IN), _deal(1, kr.DEAL_ENTRY_OUT, time=later, profit=50.0),
            _deal(2, kr.DEAL_ENTRY_IN, symbol="US100_Spot"),
            _deal(2, kr.DEAL_ENTRY_OUT, symbol="US100_Spot", time=later),
            _deal(3, kr.DEAL_ENTRY_IN, comment="setup_xauusd_orb_M15_BUY"),
            _deal(3, kr.DEAL_ENTRY_OUT, time=later),
            _deal(4, kr.DEAL_ENTRY_IN, comment=reverse_comment(PREFIX, "1")),
            _deal(4, kr.DEAL_ENTRY_OUT, time=later),
            _deal(5, kr.DEAL_ENTRY_IN),                                     # still open
            _deal(6, kr.DEAL_ENTRY_IN, time=RULE.adopted - timedelta(minutes=1)),
            _deal(6, kr.DEAL_ENTRY_OUT, time=later),
        ]

        assert [t.position for t in _pair(deals)] == [1]

    def test_a_trade_without_a_stop_has_unknown_r_not_zero(self) -> None:
        deals = [_deal(7, kr.DEAL_ENTRY_IN, order=999),
                 _deal(7, kr.DEAL_ENTRY_OUT, time=T0 + timedelta(hours=1), profit=-80.0)]

        (trade,) = _pair(deals)

        assert trade.risk is None and trade.r is None

    def test_trades_come_back_oldest_first(self) -> None:
        deals = []
        for pos, hours in ((1, 5), (2, 1), (3, 3)):
            deals += [_deal(pos, kr.DEAL_ENTRY_IN, time=T0 + timedelta(hours=hours)),
                      _deal(pos, kr.DEAL_ENTRY_OUT, time=T0 + timedelta(hours=hours + 1))]

        assert [t.position for t in _pair(deals)] == [2, 3, 1]

    @pytest.mark.skipif(not SERVER_ON_UTC_PLUS_3, reason="the expected offsets are the server's September UTC+3")
    def test_the_adoption_boundary_is_read_in_real_utc_not_broker_time(self) -> None:
        # 08:00 on the broker's clock is 05:00 UTC -- before the 06:59 adoption. Read raw, the
        # stamp says 08:00 and this trade would wrongly count against the new bot.
        stamp = datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
        deals = [_deal(9, kr.DEAL_ENTRY_IN, time=stamp),
                 _deal(9, kr.DEAL_ENTRY_OUT, time=stamp + timedelta(hours=1), profit=100.0)]

        assert _pair(deals, to_utc=kr.deal_time_utc) == []
        assert len(_pair(deals, to_utc=_as_utc)) == 1   # the naive reading would have kept it


class TestEvaluate:
    def test_no_trades_yet_is_ok(self) -> None:
        verdict = kr.evaluate(RULE, [])

        assert (verdict.status, verdict.n, verdict.net_r) == (kr.OK, 0, 0.0)

    def test_drawdown_is_measured_from_the_running_peak_including_the_flat_start(self) -> None:
        assert kr.max_drawdown([-1.0, -1.0, -1.0]) == pytest.approx(3.0)
        assert kr.max_drawdown([5.0, -2.0, -2.0, 4.0, -3.0]) == pytest.approx(4.0)

    def test_exactly_the_limit_is_not_a_breach(self) -> None:
        assert kr.evaluate(RULE, _trades([-1.0] * 17)).status == kr.OK

    def test_one_more_loss_past_the_limit_stops_the_bot(self) -> None:
        verdict = kr.evaluate(RULE, _trades([-1.0] * 18))

        assert verdict.status == kr.STOP
        assert verdict.clauses[0].breached

    def test_a_drawdown_after_trade_40_only_counts_against_the_80_trade_stop(self) -> None:
        clean_40 = [0.2] * 40                       # +8R, no drawdown, checkpoint passed
        within_22 = kr.evaluate(RULE, _trades(clean_40 + [-1.0] * 20))
        past_22 = kr.evaluate(RULE, _trades(clean_40 + [-1.0] * 23))

        assert within_22.status == kr.OK            # 20R down, but after trade 40 and under 22R
        assert not within_22.clauses[0].breached
        assert past_22.status == kr.STOP
        assert past_22.clauses[1].breached and not past_22.clauses[0].breached

    def test_the_checkpoint_waits_for_its_40th_trade(self) -> None:
        verdict = kr.evaluate(RULE, _trades([-10.0]))

        assert verdict.status == kr.OK
        assert "39 trade qalib" in verdict.clauses[2].detail

    def test_the_checkpoint_stops_below_its_floor_and_passes_on_it(self) -> None:
        below = kr.evaluate(RULE, _trades([-6.0] + [0.0] * 39))
        on_it = kr.evaluate(RULE, _trades([-5.8] + [0.0] * 39))

        assert below.status == kr.STOP and below.clauses[2].breached
        assert on_it.status == kr.OK and not on_it.clauses[2].breached

    def test_a_clean_run_to_the_horizon_asks_for_a_review(self) -> None:
        assert kr.evaluate(RULE, _trades([0.1] * 80)).status == kr.REVIEW

    def test_a_breach_outranks_reaching_the_horizon(self) -> None:
        assert kr.evaluate(RULE, _trades([-1.0] * 18 + [1.0] * 62)).status == kr.STOP

    def test_one_unknown_r_leaves_the_rule_unevaluated_rather_than_counting_zero(self) -> None:
        trades = _trades([-1.0] * 5)
        trades.append(kr.LiveTrade(position=77, opened=T0 + timedelta(days=9), long=True,
                                   net=-50.0, risk=None))

        verdict = kr.evaluate(RULE, trades)

        assert verdict.status == kr.NOT_EVALUATED
        assert verdict.unknown == (77,)

    def test_below_peak_is_where_the_bot_stands_now(self) -> None:
        verdict = kr.evaluate(RULE, _trades([3.0, -1.0, -1.0]))

        assert verdict.net_r == pytest.approx(1.0)
        assert verdict.below_peak_r == pytest.approx(2.0)


class TestReportLines:
    def test_an_ok_run_says_so(self) -> None:
        assert kr.report_lines(RULE, _trades([1.0, -1.0]))[-1].endswith("VEZIYYET: OK")

    def test_a_breach_says_stop(self) -> None:
        assert "DAYAN" in kr.report_lines(RULE, _trades([-1.0] * 18))[-1]

    def test_an_unevaluated_rule_names_the_trade_and_refuses_to_guess(self) -> None:
        trades = [kr.LiveTrade(position=4242, opened=T0, long=True, net=-10.0, risk=None)]

        lines = kr.report_lines(RULE, trades)

        assert "4242" in lines[1]
        assert "QIYMETLENDIRILMEDI" in lines[-1]
