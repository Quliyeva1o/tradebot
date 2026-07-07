"""Notification Outbound Port."""

from typing import Protocol

from application.dto.execution_dto import ExecutionDTO
from application.dto.signal_dto import SignalDTO


class INotificationPort(Protocol):
    """Outbound port for messaging and alert channels."""

    def notify_signal_generated(self, signal: SignalDTO) -> None:
        """Dispatches an alert for a generated signal.

        Args:
            signal: Generated signal details.
        """
        ...

    def notify_order_executed(self, receipt: ExecutionDTO) -> None:
        """Dispatches an alert for a trade execution receipt.

        Args:
            receipt: Executed order details.
        """
        ...
