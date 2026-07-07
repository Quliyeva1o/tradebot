"""State Repository Outbound Port."""

from typing import Protocol

from application.dto.execution_dto import ExecutionDTO
from core.models import Bar, Timeframe


class IStateRepositoryPort(Protocol):
    """Outbound port for state storage and historical retrieval."""

    def save_market_state(
        self, symbol: str, timeframe: Timeframe, bars: list[Bar]
    ) -> None:
        """Saves current synchronized market price states.

        Args:
            symbol: Target symbol asset.
            timeframe: Scoped timeframe context.
            bars: List of candlestick records.
        """
        ...

    def save_execution_receipt(self, receipt: ExecutionDTO) -> None:
        """Persists trade execution logs.

        Args:
            receipt: Transaction execution details.
        """
        ...
