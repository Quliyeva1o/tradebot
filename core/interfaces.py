"""Core interface protocols for the trading framework.

Declares the architecture interfaces implemented by indicators, strategies,
execution venues, and data feeds.
"""

from collections.abc import Callable
from typing import Protocol, runtime_checkable

import pandas as pd

from core.models import AccountInfo, Bar, Order, Position, Tick, Timeframe


@runtime_checkable
class IDataFeed(Protocol):
    """Protocol for fetching historical rates and subscribing to real-time events."""

    def get_historical_data(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: pd.Timestamp | str,
        end: pd.Timestamp | str,
    ) -> pd.DataFrame:
        """Fetches historical bars as a DataFrame."""
        ...

    def subscribe_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        callback: Callable[[Bar], None],
    ) -> None:
        """Registers a callback for completed bar updates."""
        ...

    def subscribe_ticks(
        self,
        symbol: str,
        callback: Callable[[Tick], None],
    ) -> None:
        """Registers a callback for price tick updates."""
        ...


@runtime_checkable
class IExecutionProvider(Protocol):
    """Protocol representing a brokerage execution interface."""

    def place_order(self, order: Order) -> Order:
        """Submits an order request for execution."""
        ...

    def cancel_order(self, order_id: str) -> bool:
        """Cancels a pending limit or stop order."""
        ...

    def close_position(self, position_id: str) -> bool:
        """Closes an open position."""
        ...

    def get_open_positions(self) -> list[Position]:
        """Retrieves active positions."""
        ...

    def get_pending_orders(self) -> list[Order]:
        """Retrieves active pending orders."""
        ...

    def get_account_info(self) -> AccountInfo:
        """Retrieves account balance and margin metrics."""
        ...


@runtime_checkable
class IStrategy(Protocol):
    """Protocol for strategy definitions."""

    def on_init(self, execution_provider: IExecutionProvider, data_feed: IDataFeed) -> None:
        """Strategy initialization lifecycle hook."""
        ...

    def on_bar(self, bar: Bar, symbol: str, timeframe: Timeframe) -> None:
        """Bar completion lifecycle hook."""
        ...

    def on_tick(self, tick: Tick, symbol: str) -> None:
        """Price tick lifecycle hook."""
        ...

    def on_deinit(self) -> None:
        """Strategy deinitialization lifecycle hook."""
        ...
