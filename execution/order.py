"""Order domain model: tracks an OrderRequest through its execution lifecycle.

Separate from execution.models.OrderRequest/OrderResult (the IBroker
request/response DTOs) and from core.models.Order/OrderStatus (unvalidated,
currently-unused domain-wide models) -- this is the stateful lifecycle
object a broker implementation (e.g. PaperBroker) uses internally to track
an order from submission through to its terminal outcome, with status
transitions validated the same way Position/OrderRequest validate their
fields: fail fast, never silently no-op.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, auto

from core.validation import require_positive
from execution.models import OrderRequest


class OrderStatus(Enum):
    """Lifecycle status of an Order."""

    PENDING = auto()
    FILLED = auto()
    REJECTED = auto()
    CANCELLED = auto()


# Only PENDING has outgoing transitions -- every other status is terminal.
_ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset(
        {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED}
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}


@dataclass
class Order:
    """Tracks a single OrderRequest's lifecycle from submission to a terminal status.

    Mutable by design (unlike the frozen DTOs in execution/models.py): status,
    filled_at, and fill_price change over the order's life, but only via
    fill()/reject()/cancel(), which enforce _ALLOWED_TRANSITIONS -- an
    invalid transition (e.g. cancelling an already-FILLED order) raises
    ValueError rather than silently doing nothing, so a caller bug surfaces
    immediately instead of leaving stale/inconsistent state.
    """

    order_id: str
    request: OrderRequest
    status: OrderStatus = OrderStatus.PENDING
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    filled_at: datetime | None = None
    fill_price: float | None = None

    def fill(self, fill_price: float, filled_at: datetime | None = None) -> None:
        """Transitions PENDING -> FILLED.

        Args:
            fill_price: The executed price. Must be strictly positive.
            filled_at: Fill timestamp. Defaults to now (UTC); pass explicitly
                when replaying a persisted fill (see PaperBroker's state
                deserialization).

        Raises:
            ValueError: If fill_price is not strictly positive, or the order
                is not currently PENDING.
        """
        require_positive(fill_price, "fill_price")
        self._transition(OrderStatus.FILLED)
        self.fill_price = fill_price
        self.filled_at = filled_at if filled_at is not None else datetime.now(UTC)

    def reject(self) -> None:
        """Transitions PENDING -> REJECTED.

        Raises:
            ValueError: If the order is not currently PENDING.
        """
        self._transition(OrderStatus.REJECTED)

    def cancel(self) -> None:
        """Transitions PENDING -> CANCELLED.

        Raises:
            ValueError: If the order is not currently PENDING.
        """
        self._transition(OrderStatus.CANCELLED)

    def _transition(self, new_status: OrderStatus) -> None:
        """Applies new_status if allowed from the current status, else raises.

        Raises:
            ValueError: If new_status is not reachable from self.status.
        """
        if new_status not in _ALLOWED_TRANSITIONS[self.status]:
            raise ValueError(
                f"Invalid order status transition: {self.status.name} -> "
                f"{new_status.name} (order_id={self.order_id!r})."
            )
        self.status = new_status
