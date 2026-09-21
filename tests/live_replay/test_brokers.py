"""Which broker a replay reads, and which terminal may fill its tick cache."""

import json
from pathlib import Path

import backtest.live_replay.brokers as brokers
from backtest.live_replay.brokers import Broker, broker_for, tick_cache
from backtest.live_replay.configs import REPO, parse_bat


def test_the_deployed_bot_is_replayed_on_cfi() -> None:
    assert broker_for(parse_bat(REPO / "run_live_orb_breakoutwf_xauusd_demo.bat")).name == "cfi"


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


def test_ticks_are_fetched_under_the_brokers_own_ticker(tmp_path, monkeypatch) -> None:
    asked: list[str] = []
    monkeypatch.setattr(brokers, "mt5_fetch", lambda symbol, a, b: asked.append(symbol) or [])
    tick_cache(_cfi_like(tmp_path), logged_into="CFI11-Demo").window("XAUUSD", 1_790_000_000, 60)
    assert asked == ["XAUUSD_"]
