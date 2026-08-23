"""Regression tests proving the log-file isolation fixture actually works.

tests/conftest.py's _no_real_execution_or_trade_log_files, specifically.
Two layers of proof, mirroring how thoroughly this matters: (1) the target
loggers genuinely have no FileHandler attached during a test (the mechanism
itself), and (2) directly calling the real logging functions that would
normally write to logs/execution_events.log, logs/run_live_demo.log,
logs/trade_events.log, and logs/live_signal_check.log leaves the real files
completely untouched (the actual guarantee that matters).

Deliberately does NOT assert anything about logs/data_quality_events.log --
that logger has no isolation fixture (a separately-flagged, out-of-scope
finding; see the Sprint 7 log-isolation report), so it is expected to still
grow.
"""

import logging
from pathlib import Path

import pytest

import live_signal_check
import run_live_demo
from core.models import OrderType
from execution.event_log import log_fill

_ISOLATED_LOGGER_NAMES = ("execution_events", "run_live_demo", "trade_events", "live_signal_check")


class TestNoFileHandlerAttachedDuringTests:
    """The mechanism itself: no FileHandler on any of the four loggers while a test runs."""

    @pytest.mark.parametrize("logger_name", _ISOLATED_LOGGER_NAMES)
    def test_logger_has_no_file_handler(self, logger_name: str) -> None:
        logger = logging.getLogger(logger_name)
        file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        assert file_handlers == []


class TestRealLogFilesAreUntouched:
    """The actual guarantee: calling the real logging functions leaves the real files alone."""

    def _size(self, path: Path) -> int | None:
        return path.stat().st_size if path.exists() else None

    def test_log_fill_does_not_grow_the_real_execution_events_log(self) -> None:
        real_log = Path("logs") / "execution_events.log"
        size_before = self._size(real_log)

        log_fill(
            broker="PaperBroker",
            event="open",
            order_id="log-isolation-test",
            symbol="TESTSYMBOL",
            order_type=OrderType.BUY_MARKET,
            volume=1.0,
            intended_price=100.0,
            actual_price=100.5,
        )

        assert self._size(real_log) == size_before

    def test_run_live_demo_logger_does_not_grow_the_real_run_live_demo_log(self) -> None:
        real_log = Path("logs") / "run_live_demo.log"
        size_before = self._size(real_log)

        run_live_demo.logger.info("log-isolation-test: this must never reach the real file")

        assert self._size(real_log) == size_before

    def test_log_trade_event_does_not_grow_the_real_trade_events_log(self) -> None:
        real_log = Path("logs") / "trade_events.log"
        size_before = self._size(real_log)

        run_live_demo._log_trade_event("log_isolation_test", symbol="TESTSYMBOL")

        assert self._size(real_log) == size_before

    def test_live_signal_check_logger_does_not_grow_the_real_live_signal_check_log(self) -> None:
        """live_signal_check.logger, not just execution/trade-event loggers.

        Covers the cross-file gap discovered while building this fix:
        run_live_demo.py reuses live_signal_check.check_data_quality_and_alert()
        unchanged, which logs via live_signal_check.logger directly -- a
        test in ANY file driving that code path must not leak here either.
        """
        real_log = Path("logs") / "live_signal_check.log"
        size_before = self._size(real_log)

        live_signal_check.logger.warning("log-isolation-test: this must never reach the real file")

        assert self._size(real_log) == size_before
