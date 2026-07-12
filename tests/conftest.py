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
    stub.copy_rates_range = lambda *args, **kwargs: None
    sys.modules["MetaTrader5"] = stub
