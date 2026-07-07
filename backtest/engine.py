"""Backtest driving execution engine."""

from datetime import datetime

from core.interfaces import IStrategy


class BacktestEngine:
    """Simulates market environments, feeding historical rates chronologically."""

    def __init__(self, strategy: IStrategy) -> None:
        """Initializes the BacktestEngine.

        Args:
            strategy: Trading algorithm implementation.
        """
        self.strategy = strategy

    def run(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
    ) -> dict[str, float]:
        """Runs the historical simulation.

        Args:
            symbol: Target financial symbol.
            start_time: Start date limit.
            end_time: End date limit.

        Returns:
            A dictionary containing performance indicators (e.g. final balance, drawdown).
        """
        raise NotImplementedError(
            "Backtest execution logic will be implemented in a future sprint."
        )

