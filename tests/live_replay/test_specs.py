"""The contract/lot/swap facts the replay prices everything with."""

from dataclasses import replace

import pytest

from backtest.live_replay.specs import load_specs
from tests.live_replay._fixtures import NDX


def test_one_tick_is_worth_what_the_connector_derives() -> None:
    # mt5/connector.py's _tick_value returned 0.2 for NDX100 on 2026-09-15.
    assert NDX.constraints(1.0).tick_value == pytest.approx(0.2)


def test_a_yen_contract_is_converted_to_dollars() -> None:
    jp = replace(NDX, symbol="JP225", contract_size=10.0, profit_currency="JPY")
    assert jp.usd_per_price_unit(1.0, 1 / 154.8) == pytest.approx(0.0646, abs=1e-4)


def test_one_point_on_a_tenth_of_a_lot_is_two_dollars_on_ndx100() -> None:
    assert NDX.usd_per_price_unit(0.1, 1.0) == pytest.approx(2.0)


def test_margin_scales_with_notional() -> None:
    assert NDX.margin_usd(0.5, 29000.0, 1.0) == pytest.approx(0.5 * 20 * 29000 * 0.01)


def test_the_committed_snapshot_covers_every_replayed_symbol() -> None:
    specs = load_specs()
    assert set(specs) >= {"XAUUSD", "NDX100", "SPX500", "DJI30", "GER40", "JP225"}
    assert specs["XAUUSD"].swap_mode == 1, "gold swaps in points"
    assert specs["NDX100"].swap_mode == 5, "indices swap as an annual percentage"
    assert specs["XAUUSD"].commission_per_lot_usd == 5.0
    assert specs["GER40"].profit_currency == "EUR" and specs["JP225"].profit_currency == "JPY"
