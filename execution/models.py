"""Data models for the execution layer.

These are the request/response DTOs that cross the IBroker boundary. They are
deliberately separate from core.models.Order/Position: those are unvalidated
domain-wide models used throughout read paths (backtesting, data engine),
while these are boundary-crossing values exchanged with a live execution
venue, where a malformed value (e.g. negative volume) must fail fast at
construction instead of being silently sent to a broker.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from core.models import OrderType
from core.validation import require_non_negative, require_positive


@dataclass(frozen=True)
class OrderRequest:
    """Represents an order to submit to a broker via IBroker.place_order()."""

    symbol: str
    order_type: OrderType
    volume: float
    price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    deviation: int = 10
    comment: str = ""

    def __post_init__(self) -> None:
        """Validates parameter ranges.

        Raises:
            ValueError: If volume is not strictly positive, deviation is
                negative, or price/stop_loss/take_profit is provided but
                not strictly positive.
        """
        require_positive(self.volume, "volume")
        require_non_negative(self.deviation, "deviation")
        if self.price is not None:
            require_positive(self.price, "price")
        if self.stop_loss is not None:
            require_positive(self.stop_loss, "stop_loss")
        if self.take_profit is not None:
            require_positive(self.take_profit, "take_profit")


@dataclass(frozen=True)
class OrderResult:
    """Represents a broker's response to a submitted OrderRequest.

    success distinguishes a genuine broker-communicated rejection (e.g. no
    money, invalid stops -- the venue responded, just declined the trade)
    from an infrastructure failure, which an IBroker implementation raises
    instead of encoding here.
    """

    success: bool
    order_id: str = ""
    position_id: str = ""
    price: float = 0.0
    volume: float = 0.0
    retcode: int = 0
    comment: str = ""

    def __post_init__(self) -> None:
        """Validates parameter ranges.

        Raises:
            ValueError: If price or volume is negative.
        """
        require_non_negative(self.price, "price")
        require_non_negative(self.volume, "volume")


class TradeManagerAction(Enum):
    """Outcome of a single TradeManager.on_new_bar()/close_trade() call.

    Mirrors backtest/models.py's TradeResult (WIN/LOSS/EXPIRED): a small,
    string-valued, closed-set outcome enum with no attached data -- callers
    that need details (fill price, P&L) read them off the Order/OrderResult
    returned separately.
    """

    HELD = "HELD"
    CLOSED_SL = "CLOSED_SL"
    CLOSED_TP = "CLOSED_TP"
    CLOSED_MANUAL = "CLOSED_MANUAL"


@dataclass(frozen=True)
class Position:
    """Represents an active open position reported by a broker/execution venue."""

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

    def __post_init__(self) -> None:
        """Validates parameter ranges.

        Raises:
            ValueError: If volume or open_price is not strictly positive,
                current_price is negative, or stop_loss/take_profit is
                provided but not strictly positive.
        """
        require_positive(self.volume, "volume")
        require_positive(self.open_price, "open_price")
        require_non_negative(self.current_price, "current_price")
        if self.stop_loss is not None:
            require_positive(self.stop_loss, "stop_loss")
        if self.take_profit is not None:
            require_positive(self.take_profit, "take_profit")
