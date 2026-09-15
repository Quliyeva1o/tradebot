"""How the broker prices a market entry, a stop/target exit, swap and lot size."""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from backtest.live_replay.pricing import (
    entry_price, exit_on_bar, rollover_days, size_position, swap_usd,
)
from core.models import Bar, SignalDirection, Timeframe
from strategy.models import TradeSetup
from tests.live_replay._fixtures import NDX, XAU


def _bar(o: float, h: float, low: float, c: float) -> Bar:
    return Bar(timestamp=datetime(2026, 9, 14, 14, 31, tzinfo=UTC), open=o, high=h, low=low,
               close=c, volume=1.0)


def _setup(entry: float, stop: float, target: float,
           direction: SignalDirection = SignalDirection.BUY) -> TradeSetup:
    return TradeSetup(setup_id="s", symbol="NDX100", timeframe=Timeframe.M5, direction=direction,
                      entry_zone=(entry, entry), stop_zone=(stop, stop), target_zone=(target, target),
                      confidence_score=1.0, confluence=[], trigger_reason="", invalidations=[],
                      related_structure_break=None, related_order_block=None, related_fvg=None,
                      timestamp=datetime(2026, 9, 14, 14, 5, tzinfo=UTC))


def test_a_buy_pays_the_ask_and_a_sell_gets_the_bid() -> None:
    bar = _bar(100.0, 101.0, 99.0, 100.5)
    assert entry_price(SignalDirection.BUY, bar, 3.0) == 103.0
    assert entry_price(SignalDirection.SELL, bar, 3.0) == 100.0


def test_a_short_stop_triggers_on_the_ask_even_when_the_bid_never_reaches_it() -> None:
    # bid high 199 stays under the 200 stop; the ask (+2) crosses it
    hit = exit_on_bar(SignalDirection.SELL, 200.0, 150.0, _bar(195.0, 199.0, 194.0, 196.0), 2.0,
                      entry_bar=False, after_break=False)
    assert hit == (200.0, "SL")


def test_a_long_stop_ignores_the_spread() -> None:
    assert exit_on_bar(SignalDirection.BUY, 90.0, 150.0, _bar(95.0, 96.0, 90.5, 91.0), 2.0,
                       entry_bar=False, after_break=False) is None


def test_a_stop_after_a_trading_break_fills_at_the_close_when_that_is_worse() -> None:
    assert exit_on_bar(SignalDirection.BUY, 90.0, 150.0, _bar(95.0, 95.0, 80.0, 82.0), 0.0,
                       entry_bar=False, after_break=True) == (82.0, "SL")


def test_index_swap_days_are_one_per_weeknight_and_three_on_friday() -> None:
    assert rollover_days(date(2026, 9, 10), date(2026, 9, 11), 5) == 1
    assert rollover_days(date(2026, 9, 11), date(2026, 9, 14), 5) == 3


def test_gold_triples_on_wednesday_not_friday() -> None:
    assert rollover_days(date(2026, 9, 11), date(2026, 9, 14), 3) == 1
    assert rollover_days(date(2026, 9, 9), date(2026, 9, 10), 3) == 3


def test_one_night_of_index_swap_matches_the_real_deal() -> None:
    # deal 12377152: 0.01 NDX100 lots, Thursday to Friday, swap -1.19
    assert swap_usd(NDX, SignalDirection.BUY, 0.01, 29210.38, 1, 1.0) == pytest.approx(-1.19, abs=0.01)


def test_a_weekend_of_index_swap_matches_the_real_deal() -> None:
    # deal 12494940: 0.01 NDX100 lots held Friday to Monday, swap -3.59
    assert swap_usd(NDX, SignalDirection.BUY, 0.01, 29466.30, 3, 1.0) == pytest.approx(-3.59, abs=0.02)


def test_gold_swap_in_points_matches_the_real_deal() -> None:
    # deal 12048249: 0.06 XAUUSD lots, one night, -4.08
    assert swap_usd(XAU, SignalDirection.BUY, 0.06, 4408.17, 1, 1.0) == pytest.approx(-4.08, abs=0.01)


def test_size_risks_half_a_percent_of_the_balance() -> None:
    # $250 budget; a 197.37-point stop costs $3,947.4 a lot -> 0.0633 -> 0.06 after the step
    assert size_position(NDX, _setup(29048.08, 28850.71, 29837.56), 50_000.0, 1.0, 0.005) == pytest.approx(0.06)


def test_the_margin_ceiling_scales_an_oversized_entry_down() -> None:
    # A 1-point stop buys 12.5 lots, whose margin at 5% is 36x the 20% ceiling on $50k
    volume = size_position(replace(NDX, margin_rate=0.05), _setup(29048.08, 29047.08, 29052.08),
                           50_000.0, 1.0, 0.005)
    assert volume == pytest.approx(0.34)


def test_a_setup_whose_stop_equals_its_entry_cannot_be_sized() -> None:
    assert size_position(NDX, _setup(29048.08, 29048.08, 29100.0), 50_000.0, 1.0, 0.005) == 0.0
