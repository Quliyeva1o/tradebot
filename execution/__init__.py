"""Execution package.

Defines the IBroker interface decoupling strategy/trade-management logic
from any specific execution venue, concrete broker adapters (MT5Broker,
PaperBroker), TradeManager (which owns an open trade's lifecycle against any
IBroker), and the swappable StopEngine/TakeProfitEngine interfaces
TradeManager resolves SL/TP levels through.
"""

from execution.interfaces import IBroker
from execution.models import OrderRequest, OrderResult, Position, TradeManagerAction
from execution.mt5_broker import MT5Broker
from execution.order import Order, OrderStatus
from execution.paper_broker import PaperBroker
from execution.stop_engine import FixedStopEngine, StopEngine
from execution.take_profit_engine import FixedTakeProfitEngine, TakeProfitEngine
from execution.trade_manager import TradeManager

__all__ = [
    "FixedStopEngine",
    "FixedTakeProfitEngine",
    "IBroker",
    "MT5Broker",
    "Order",
    "OrderRequest",
    "OrderResult",
    "OrderStatus",
    "PaperBroker",
    "Position",
    "StopEngine",
    "TakeProfitEngine",
    "TradeManager",
    "TradeManagerAction",
]
