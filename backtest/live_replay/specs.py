"""Contract, lot, swap and margin facts for each replayed symbol, as MT5 reported them.

A snapshot rather than a live query: a replay must be reproducible without the terminal, and
these change rarely. scripts/capture_symbol_specs.py rewrites symbol_specs.json.

Historical swap rates are not published by the broker, so today's rates are applied to the
whole history -- a stated limitation of the replay, not an oversight.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from core.models import SymbolConstraints

SPECS_FILE = Path(__file__).with_name("symbol_specs.json")


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    point: float
    tick_size: float
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    profit_currency: str
    swap_mode: int            # 1 = points per lot per day, 5 = annual percent of the price
    swap_long: float
    swap_short: float
    swap_rollover3days: int   # MT5 weekday numbering: 0 = Sunday, 3 = Wednesday, 5 = Friday
    margin_rate: float        # margin / notional, in account currency
    commission_per_lot_usd: float

    def constraints(self, usd_per_unit: float) -> SymbolConstraints:
        """What PositionSizer needs, with the tick value in USD at this FX rate."""
        return SymbolConstraints(
            symbol=self.symbol, contract_size=self.contract_size, tick_size=self.tick_size,
            tick_value=self.contract_size * self.tick_size * usd_per_unit,
            volume_min=self.volume_min, volume_max=self.volume_max, volume_step=self.volume_step,
        )

    def usd_per_price_unit(self, volume: float, usd_per_unit: float) -> float:
        """Dollars one whole price unit is worth on `volume` lots."""
        return volume * self.contract_size * usd_per_unit

    def margin_usd(self, volume: float, price: float, usd_per_unit: float) -> float:
        return volume * self.contract_size * price * usd_per_unit * self.margin_rate


def load_specs(path: Path = SPECS_FILE) -> dict[str, SymbolSpec]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    fields = set(SymbolSpec.__dataclass_fields__) - {"symbol"}
    return {symbol: SymbolSpec(symbol=symbol, **{k: v for k, v in row.items() if k in fields})
            for symbol, row in raw["symbols"].items()}
