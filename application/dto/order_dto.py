"""Order Data Transfer Object."""

from dataclasses import dataclass, field
from datetime import datetime

from core.models import OrderType


@dataclass(frozen=True)
class OrderDTO:
    """DTO representing an order execution request sent to the broker."""

    order_id: str
    symbol: str
    order_type: OrderType
    volume: float
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    timestamp: datetime = field(default_factory=datetime.now)
