"""Which broker a replay reads, and which terminal may fill its tick cache."""

import json
from pathlib import Path

import pytest

import backtest.live_replay.brokers as brokers
import config.brokers as machine
from backtest.live_replay.brokers import Broker, deployed_broker, tick_cache


def test_the_replay_follows_the_machine_rather_than_the_launcher(monkeypatch) -> None:
    """Since 2026-09-21 the same launchers run on the VPS's CFI account and on this
    workstation's FundingPips one, so nothing in a .bat says which prices are the bot's."""
    monkeypatch.setattr(machine, "local", lambda: machine.profiles()["fundingpips"])
    assert deployed_broker().name == "fundingpips"

    monkeypatch.setattr(machine, "local", lambda: machine.profiles()["cfi"])
    assert deployed_broker().name == "cfi"


def test_the_other_machines_deployment_can_be_named() -> None:
    """The workstation judges the VPS's real-order bot with --broker cfi."""
    assert deployed_broker("cfi").name == "cfi"
    with pytest.raises(LookupError):
        deployed_broker("fxtm")


def _cfi_like(tmp_path: Path) -> Broker:
    specs = tmp_path / "specs.json"
    specs.write_text(json.dumps({"server": "CFI11-Demo", "symbols": {"XAUUSD": {
        "point": 0.001, "tick_size": 0.001, "contract_size": 100.0, "volume_min": 0.01,
        "volume_step": 0.01, "volume_max": 20.0, "profit_currency": "USD", "swap_mode": 1,
        "swap_long": -590.835, "swap_short": 331.36, "swap_rollover3days": 3, "margin_rate": 0.01,
        "commission_per_lot_usd": 0.0, "broker_symbol": "XAUUSD_"}}}), encoding="utf-8")
    return Broker("cfi", "CFI", tmp_path / "data", specs)


def test_ticks_are_never_fetched_from_a_terminal_on_another_broker(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(brokers, "mt5_fetch", lambda *a: (_ for _ in ()).throw(AssertionError("fetched")))
    cache = tick_cache(_cfi_like(tmp_path), logged_into="FundingPips-Trial")
    assert cache.window("XAUUSD", 1_790_000_000, 60) == []
    assert not any(tmp_path.rglob("*.csv"))


def test_ticks_are_fetched_under_the_brokers_own_ticker_and_clock(tmp_path, monkeypatch) -> None:
    asked: list[tuple] = []
    monkeypatch.setattr(brokers, "mt5_fetch", lambda symbol, a, b, clock: asked.append((symbol, clock)) or [])
    tick_cache(_cfi_like(tmp_path), logged_into="CFI11-Demo").window("XAUUSD", 1_790_000_000, 60)
    assert asked == [("XAUUSD_", machine.SERVER_CLOCKS["cfi"])]
