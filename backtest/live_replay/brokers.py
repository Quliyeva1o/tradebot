"""Which broker's prices and contract facts a replay uses.

Every broker names the same instrument its own way and prices it with its own spread, swap and
contract size, so a replay is only honest against the broker a bot actually trades on. A broker
profile says where that broker's history CSVs live and which specs file describes its contracts;
the specs file's `broker_symbol` names each history CSV.

Which broker a DEPLOYED bot trades is no longer readable from its launcher: one launcher set now
runs on two machines, the VPS on CFI and the workstation on FundingPips, and the ticker is
resolved per machine from .env (config/brokers.py). So a replay of a deployed bot is replayed on
the broker of the machine it is asked about -- this one by default, another when named.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import config.brokers as machine
from backtest.live_replay.specs import SymbolSpec, load_specs
from backtest.live_replay.ticks import TickCache, mt5_fetch


@dataclass(frozen=True)
class Broker:
    name: str
    label: str
    data_dir: Path
    specs_file: Path


BROKERS = {
    "fundingpips": Broker("fundingpips", "FundingPips (hazırkı)",
                          Path("data/history/fundingpips"),
                          Path("backtest/live_replay/symbol_specs.json")),
    "cfi": Broker("cfi", "CFI (yeni hesab)",
                  Path("data/history/cfi"),
                  Path("backtest/live_replay/symbol_specs_cfi.json")),
}


def history_path(broker: Broker, spec: SymbolSpec) -> Path:
    return broker.data_dir / f"{spec.broker_symbol or spec.symbol}_M1.csv"


def server(broker: Broker) -> str:
    """The MT5 server the broker's specs were captured on -- what account_info().server reads
    while the terminal is logged into that broker."""
    return json.loads(broker.specs_file.read_text(encoding="utf-8"))["server"]


def connected_server() -> str | None:
    """The server the MT5 terminal is logged into, or None when there is no terminal to ask."""
    try:
        import MetaTrader5 as mt5  # noqa: N813
    except ImportError:
        return None
    if not mt5.initialize():
        return None
    info = mt5.account_info()
    return None if info is None else info.server


def tick_cache(broker: Broker, logged_into: str | None) -> TickCache:
    """The broker's own tick cache, filled from MT5 only while MT5 is logged into that broker.

    A fetched window is cached for good, a miss included, so a fetch from a terminal on another
    account -- which knows no such symbol and answers nothing -- used to store "no ticks here"
    permanently. Any other terminal, or none, means the cache is read and never written. A live
    fetch asks for the broker's own ticker (XAUUSD_ on CFI), not this repo's name.
    """
    if logged_into != server(broker):
        return TickCache(broker.data_dir / "ticks", fetch=None)
    tickers = {s: spec.broker_symbol or s for s, spec in load_specs(broker.specs_file).items()}
    return TickCache(broker.data_dir / "ticks",
                     fetch=lambda symbol, start, end: mt5_fetch(tickers.get(symbol, symbol), start, end))


def deployed_broker(name: str = "") -> Broker:
    """The broker whose prices a deployed bot is replayed on.

    Default: the one THIS machine trades, read from .env exactly as the live runners read it,
    so a report generated on a machine always describes the account that machine is logged into.
    Pass a name to judge the other deployment -- from the workstation, `cfi` is the VPS's.
    """
    if name:
        if name not in BROKERS:
            raise LookupError(f"unknown broker {name!r}; known: {', '.join(sorted(BROKERS))}")
        return BROKERS[name]
    return BROKERS[machine.local().name]
