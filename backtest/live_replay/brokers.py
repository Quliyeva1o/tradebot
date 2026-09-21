"""Which broker's prices and contract facts a replay uses.

Every broker names the same instrument its own way and prices it with its own spread, swap and
contract size, so a replay is only honest against the broker a bot actually trades on. A broker
profile says where that broker's history CSVs live and which specs file describes its contracts;
the specs file's `broker_symbol` names each history CSV.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backtest.live_replay.configs import BotConfig
from backtest.live_replay.specs import SymbolSpec, load_specs


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


def broker_for(config: BotConfig) -> Broker:
    """The broker whose own ticker the launcher names.

    Matched on the specs, not assumed: a launcher with no broker ticker trades the name this
    repo uses (FundingPips'), one with a broker ticker trades whichever broker lists it.
    """
    for broker in BROKERS.values():
        spec = load_specs(broker.specs_file).get(config.symbol)
        if spec is not None and spec.broker_symbol == config.broker_ticker:
            return broker
    raise LookupError(f"{config.task}: no broker profile lists {config.broker_ticker or config.symbol}")
