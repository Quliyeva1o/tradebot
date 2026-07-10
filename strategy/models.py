"""Data models for the strategy layer."""

from dataclasses import dataclass
from datetime import datetime

from core.models import SignalDirection, Timeframe
from market_structure.structure_models import StructureBreak
from smc.fvg import FairValueGap
from smc.order_block import OrderBlock


@dataclass(frozen=True)
class TradeSetup:
    """Represents a valid, immutable market opportunity identified by a strategy.

    This model describes the opportunity zone, confluence parameters, and
    structure references. It does not calculate risk/reward or size positions.
    """

    setup_id: str
    symbol: str
    timeframe: Timeframe
    direction: SignalDirection
    entry_zone: tuple[float, float]
    stop_zone: tuple[float, float]
    target_zone: tuple[float, float]
    confidence_score: float
    confluence: list[str]
    trigger_reason: str
    invalidations: list[str]
    related_structure_break: StructureBreak | None
    related_order_block: OrderBlock | None
    related_fvg: FairValueGap | None
    timestamp: datetime
    strategy_name: str = ""
