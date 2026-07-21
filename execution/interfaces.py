"""Interfaces and protocols for the execution layer."""

from typing import Protocol, runtime_checkable

from core.models import AccountInfo
from execution.models import OrderRequest, OrderResult, Position


@runtime_checkable
class IBroker(Protocol):
    """Protocol that every broker/execution venue adapter must implement.

    Decouples strategy and trade-management logic from any specific
    execution venue (MT5, a future paper-trading simulator, another broker
    API) so a consumer can depend on this interface instead of a concrete
    connector -- the same Dependency Inversion / Open-Closed role
    TradeSetupStrategy plays for strategies.
    """

    def connect(self) -> bool:
        """Establishes a session with the execution venue.

        Returns:
            True if the connection was established successfully, False otherwise.
        """
        ...

    def place_order(self, order: OrderRequest) -> OrderResult:
        """Submits an order for execution.

        Args:
            order: The order to submit.

        Returns:
            An OrderResult describing whether the venue accepted the order.
        """
        ...

    def cancel_order(self, order_id: str) -> bool:
        """Cancels a previously placed pending order.

        Args:
            order_id: The venue-assigned identifier of the order to cancel.

        Returns:
            True if the order was canceled, False otherwise.
        """
        ...

    def close_position(self, position_id: str) -> OrderResult:
        """Closes an open position at the current market price.

        Args:
            position_id: The venue-assigned identifier of the position to
                close (Position.id / OrderResult.position_id from the order
                that opened it).

        Returns:
            An OrderResult describing whether the venue accepted the close.
        """
        ...

    def get_open_positions(self) -> list[Position]:
        """Fetches all currently open positions on the connected account.

        Returns:
            A list of currently open Position records.
        """
        ...

    def get_account_info(self) -> AccountInfo:
        """Fetches the connected account's current balance/equity/margin.

        Returns:
            An AccountInfo snapshot of the connected account.
        """
        ...
