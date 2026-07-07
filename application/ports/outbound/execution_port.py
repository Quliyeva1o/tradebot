"""Execution Outbound Port."""

from typing import Protocol

from application.dto.execution_dto import ExecutionDTO
from application.dto.order_dto import OrderDTO
from core.models import AccountInfo, Position


class IExecutionPort(Protocol):
    """Outbound port for brokerage execution venues."""

    def execute_market_order(self, order: OrderDTO) -> ExecutionDTO:
        """Executes a market order and returns an execution receipt.

        Args:
            order: Order execution details.

        Returns:
            An ExecutionDTO transaction receipt.
        """
        ...

    def cancel_pending_order(self, order_id: str) -> bool:
        """Cancels a pending order.

        Args:
            order_id: Unique broker identifier of the order.

        Returns:
            True if cancelled, False otherwise.
        """
        ...

    def fetch_active_positions(self, symbol: str | None = None) -> list[Position]:
        """Fetches active positions from the execution broker.

        Args:
            symbol: Optional target symbol filter.

        Returns:
            A list of Position objects.
        """
        ...

    def fetch_account_balance(self) -> AccountInfo:
        """Retrieves account balance and margins.

        Returns:
            AccountInfo snapshot data.
        """
        ...
