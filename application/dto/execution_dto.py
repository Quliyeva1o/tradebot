"""Execution Data Transfer Object."""

from dataclasses import dataclass, field
from datetime import datetime

from core.models import OrderStatus


@dataclass(frozen=True)
class ExecutionDTO:
    """DTO representing the broker order execution transaction receipt."""

    execution_id: str
    order_id: str
    symbol: str
    status: OrderStatus
    fill_price: float | None = None
    volume: float = 0.0
    slippage: float = 0.0
    commission: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
