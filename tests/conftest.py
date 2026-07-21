"""Stubs the MetaTrader5 package on platforms where it can't be installed
(Windows-only SDK) so test modules that import it -- but mock every actual
MT5 call -- can still be collected. No real MT5 behavior is provided here;
each test replaces the specific attributes it needs via unittest.mock.
"""

import sys
import types

try:
    import MetaTrader5  # noqa: F401
except ImportError:
    stub = types.ModuleType("MetaTrader5")
    stub.TIMEFRAME_M1 = 1
    stub.TIMEFRAME_M5 = 5
    stub.TIMEFRAME_M15 = 15
    stub.TIMEFRAME_M30 = 30
    stub.TIMEFRAME_H1 = 16385
    stub.TIMEFRAME_H4 = 16388
    stub.TIMEFRAME_D1 = 16408
    stub.initialize = lambda *args, **kwargs: False
    stub.shutdown = lambda *args, **kwargs: None
    stub.login = lambda *args, **kwargs: False
    stub.last_error = lambda: (0, "MetaTrader5 not available on this platform")
    stub.symbol_select = lambda *args, **kwargs: False
    stub.symbol_info = lambda *args, **kwargs: None
    stub.symbol_info_tick = lambda *args, **kwargs: None
    stub.copy_rates_range = lambda *args, **kwargs: None
    stub.account_info = lambda *args, **kwargs: None
    stub.order_send = lambda *args, **kwargs: None
    stub.positions_get = lambda *args, **kwargs: None
    stub.ORDER_TYPE_BUY = 0
    stub.ORDER_TYPE_SELL = 1
    stub.ORDER_TYPE_BUY_LIMIT = 2
    stub.ORDER_TYPE_SELL_LIMIT = 3
    stub.ORDER_TYPE_BUY_STOP = 4
    stub.ORDER_TYPE_SELL_STOP = 5
    stub.POSITION_TYPE_BUY = 0
    stub.POSITION_TYPE_SELL = 1
    stub.TRADE_ACTION_DEAL = 1
    stub.TRADE_ACTION_PENDING = 5
    stub.TRADE_ACTION_REMOVE = 8
    stub.TRADE_RETCODE_DONE = 10009
    stub.TRADE_RETCODE_DONE_PARTIAL = 10010
    stub.TRADE_RETCODE_PLACED = 10008
    sys.modules["MetaTrader5"] = stub
