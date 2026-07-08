"""Unit tests and structural check verification for the Application Layer."""

import sys
from collections.abc import Callable
from typing import Any

from application.dto import ExecutionDTO, OrderDTO, SignalDTO
from application.ports.inbound.coordinator_usecase import ITradingCoordinatorUseCase
from application.ports.outbound.data_feed_port import IDataFeedPort
from application.ports.outbound.execution_port import IExecutionPort
from application.ports.outbound.notification_port import INotificationPort
from application.ports.outbound.state_repository import IStateRepositoryPort
from application.services.trading_coordinator import TradingCoordinatorService
from core.models import Bar, OrderStatus, Tick, Timeframe


class MockDataFeed(IDataFeedPort):
    """Mock implementation of IDataFeedPort."""

    def fetch_historical_bars(self, symbol: str, timeframe: Timeframe, count: int) -> list[Bar]:
        """Fetch historical bars."""
        return []

    def stream_realtime_data(self, symbol: str, timeframe: Timeframe, callback: Callable[[Bar], None]) -> None:
        """Stream realtime data."""
        pass


class MockExecution(IExecutionPort):
    """Mock implementation of IExecutionPort."""

    def execute_market_order(self, order: OrderDTO) -> ExecutionDTO:
        """Execute market order."""
        return ExecutionDTO(
            execution_id="mock_exec",
            order_id=order.order_id,
            symbol=order.symbol,
            status=OrderStatus.FILLED,
            fill_price=order.price or 1.1000,
            volume=order.volume,
        )

    def cancel_pending_order(self, order_id: str) -> bool:
        """Cancel pending order."""
        return True

    def fetch_active_positions(self, symbol: str | None = None) -> list[Any]:
        """Fetch active positions."""
        return []

    def fetch_account_balance(self) -> Any:
        """Fetch account balance."""
        from core.models import AccountInfo

        return AccountInfo(10000.0, 10000.0, 0.0, 10000.0)


class MockNotifier(INotificationPort):
    """Mock implementation of INotificationPort."""

    def notify_signal_generated(self, signal: SignalDTO) -> None:
        """Notify signal generated."""
        pass

    def notify_order_executed(self, receipt: ExecutionDTO) -> None:
        """Notify order executed."""
        pass


class MockRepository(IStateRepositoryPort):
    """Mock implementation of IStateRepositoryPort."""

    def save_market_state(self, symbol: str, timeframe: Timeframe, bars: list[Bar]) -> None:
        """Save market state."""
        pass

    def save_execution_receipt(self, receipt: ExecutionDTO) -> None:
        """Save execution receipt."""
        pass


def test_coordinator_initialization() -> None:
    """Verifies that TradingCoordinatorService initializes and implements UseCase protocol."""
    coordinator = TradingCoordinatorService(
        data_feed=MockDataFeed(),
        execution_venue=MockExecution(),
        notifier=MockNotifier(),
        repository=MockRepository(),
    )

    assert isinstance(coordinator, ITradingCoordinatorUseCase)


def test_coordinator_workflow_execution() -> None:
    """Verifies that coordinator workflow triggers correctly with mock entities."""
    coordinator = TradingCoordinatorService(
        data_feed=MockDataFeed(),
        execution_venue=MockExecution(),
        notifier=MockNotifier(),
        repository=MockRepository(),
    )

    # Given
    from datetime import datetime

    bar = Bar(datetime.now(), 1.1000, 1.1050, 1.0990, 1.1020, 100.0)
    tick = Tick(datetime.now(), 1.1010, 1.1020)

    # When / Then (verify no exception is thrown in skeletal execution)
    coordinator.process_candle_close("EURUSD", Timeframe.H1, bar)
    coordinator.process_tick_update("EURUSD", tick)


def test_dependency_rules_enforcement() -> None:
    """Enforces Clean Architecture rules: application must not import infrastructure."""
    forbidden_prefixes = ["data", "mt5", "backtest", "dashboard", "tests"]

    for module_name, module in list(sys.modules.items()):
        if module_name.startswith("application.") or module_name == "application":
            # Inspect all imported names in the module scope
            for val in dir(module):
                # Ensure no forbidden import resides in the namespace
                attr = getattr(module, val)
                if hasattr(attr, "__module__") and attr.__module__:
                    module_origin = attr.__module__
                    for prefix in forbidden_prefixes:
                        parts = module_origin.split(".")
                        assert parts[0] != prefix, (
                            f"Dependency Rule Violation: {module_name} imports "
                            f"{module_origin} (violates boundary logic)"
                        )
