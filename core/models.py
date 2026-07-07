"""Core domain models for the trading framework.

This module defines structures representing candlesticks, ticks, orders, positions,
and account metrics.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto


class Timeframe(Enum):
    """Supported candle bar timeframes."""
    M1 = "M1"
    M5 = "M5"
    M15 = "M15"
    M30 = "M30"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"


class OrderType(Enum):
    """Types of trading execution and pending orders."""
    BUY_MARKET = auto()
    SELL_MARKET = auto()
    BUY_LIMIT = auto()
    SELL_LIMIT = auto()
    BUY_STOP = auto()
    SELL_STOP = auto()


class OrderStatus(Enum):
    """Execution status of an order request."""
    PENDING = auto()
    FILLED = auto()
    CANCELED = auto()
    REJECTED = auto()


@dataclass(frozen=True)
class Bar:
    """Represents a standard OHLCV price bar."""
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    spread: float = 0.0


@dataclass(frozen=True)
class Tick:
    """Represents a single market price tick update."""
    timestamp: datetime
    bid: float
    ask: float
    volume: float = 0.0


@dataclass(frozen=True)
class Order:
    """Represents an order request submitted for execution."""
    id: str
    symbol: str
    order_type: OrderType
    volume: float
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    status: OrderStatus = OrderStatus.PENDING
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class Position:
    """Represents an active open trade in the execution venue."""
    id: str
    symbol: str
    order_type: OrderType
    volume: float
    open_price: float
    current_price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    profit: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True)
class AccountInfo:
    """Represents account balances and margin metrics."""
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float = 0.0
    currency: str = "USD"
