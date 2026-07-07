"""Trading Coordinator Inbound Use Case."""

from typing import Protocol, runtime_checkable

from core.models import Bar, Tick, Timeframe


@runtime_checkable
class ITradingCoordinatorUseCase(Protocol):
    """Inbound port for coordinating market analysis and trading actions."""

    def process_candle_close(
        self, symbol: str, timeframe: Timeframe, bar: Bar
    ) -> None:
        """Processes a newly closed historical candle bar.

        Args:
            symbol: Target symbol asset (e.g. EURUSD).
            timeframe: Completed bar timeframe.
            bar: Completed candle details.
        """
        ...

    def process_tick_update(self, symbol: str, tick: Tick) -> None:
        """Processes a new incoming price tick update.

        Args:
            symbol: Target symbol asset.
            tick: Live bid/ask tick data.
        """
        ...
