"""Suite-wide pytest fixtures and platform stubs.

Stubs the MetaTrader5 package on platforms where it can't be installed
(Windows-only SDK) so test modules that import it -- but mock every actual
MT5 call -- can still be collected. No real MT5 behavior is provided here;
each test replaces the specific attributes it needs via unittest.mock.

Also holds suite-wide autouse fixtures (below) that must apply regardless of
which test file a test lives in.
"""

import logging
import sys
import types
from collections.abc import Iterator

import pytest

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


_ISOLATED_LOGGER_NAMES = (
    "execution_events",
    "run_live_demo",
    "trade_events",
    "live_signal_check",
    "trade_manager",
    "mt5_broker",
)


@pytest.fixture(autouse=True)
def _no_real_execution_or_trade_log_files() -> Iterator[None]:
    """Detaches FileHandlers for execution/trade-event loggers during every test.

    Mirrors tests/test_live_signal_check.py's _no_real_log_file exactly
    (same detach-before/reattach-after mechanism), generalized here in
    conftest.py -- suite-wide and autouse -- rather than duplicated
    per-file, so no current OR future test can accidentally reintroduce the
    leak just by being added to a new file.

    execution_events (execution/event_log.py's log_fill(), called by both
    PaperBroker and MT5Broker on every fill), run_live_demo (run_live_demo.py's
    own human-readable logger), and trade_events (run_live_demo.py's
    _log_trade_event()) are all module-level logging singletons configured
    once at import time via utils.logging.setup_logger()/
    setup_structured_logger() (see their `if logger.handlers: return logger`
    guard) -- like live_signal_check.logger, they can't be redirected per
    test with a STATE_FILE-style monkeypatch; the already-attached
    FileHandler itself must be removed and restored.

    live_signal_check is included here too, despite test_live_signal_check.py
    already having its own file-local _no_real_log_file fixture: that
    fixture only protects tests IN that file. run_live_demo.py reuses
    live_signal_check.check_data_quality_and_alert() unchanged (which logs
    via live_signal_check.logger directly), so tests/test_run_live_demo.py's
    run_once()/main() calls were writing to the real logs/live_signal_check.log
    with no local fixture to stop them -- discovered via this fix's own
    before/after byte-for-byte verification (see the Sprint 7 log-isolation
    report), not something the task anticipated. Covering it here too closes
    that gap with the same mechanism at negligible extra cost, rather than
    leaving a newly-found active leak unaddressed.

    trade_manager (execution/trade_manager.py) and mt5_broker
    (execution/mt5_broker.py) were added later, once their own rejection-path
    logger.error() calls were given log_to_file=True -- a rejected real order's
    retcode/comment (e.g. the 2026-07-27 SELL USTEC "Trade disabled" rejection)
    was previously only ever printed to console and lost, since neither
    logger persisted to a file at all; without adding them here too, every
    test exercising a broker rejection (place_order()/close_position()
    returning success=False) would start leaking into the real, repo-relative
    logs/trade_manager.log and logs/mt5_broker.log the same way the four
    loggers below already had to be protected against.

    Without this, any test that opens/closes a PaperBroker or MT5Broker
    position, or drives run_live_demo.py's run_once()/main() (currently:
    tests/test_event_log.py, tests/test_paper_broker.py,
    tests/test_mt5_broker.py, tests/test_trade_manager.py,
    tests/test_run_live_demo.py -- and any future test exercising the same
    code), would land in the real, repo-relative logs/execution_events.log,
    logs/run_live_demo.log, logs/trade_events.log, logs/live_signal_check.log,
    logs/trade_manager.log, and logs/mt5_broker.log -- files a human operator
    relies on to analyze real demo-account slippage/trade history (see the
    Sprint 7 slippage-analysis report), polluted with fake test order_ids and
    synthetic bar timestamps.
    """
    detached: list[tuple[logging.Logger, list[logging.FileHandler]]] = []
    for name in _ISOLATED_LOGGER_NAMES:
        logger = logging.getLogger(name)
        file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        for handler in file_handlers:
            logger.removeHandler(handler)
        detached.append((logger, file_handlers))

    yield

    for logger, file_handlers in detached:
        for handler in file_handlers:
            logger.addHandler(handler)
