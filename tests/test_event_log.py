"""Unit tests for execution/event_log.py (structured fill/slippage logging).

Sprint 6c, T2. Includes the required "known fill scenario" test proving
log_fill() computes the correct intended-vs-actual delta and its
account-currency cost.
"""

import json
import logging
from typing import cast

import pytest

from core.models import OrderType
from execution.event_log import log_fill
from utils.logging import JsonFormatter


class TestLogFillSlippageComputation:
    """Tests for log_fill()'s realized-slippage-cost computation."""

    def test_buy_fill_worse_than_intended_is_a_positive_cost(self) -> None:
        """Known fill scenario: BUY intended @ 29000.00, actually filled @ 29001.50.

        Paid more (adverse), volume 2.0:
        adverse_price_delta = 29001.50 - 29000.00 = 1.50
        realized_slippage_cost = 1.50 * 2.0 = 3.00
        """
        cost = log_fill(
            broker="PaperBroker",
            event="open",
            order_id="o1",
            symbol="USTEC",
            order_type=OrderType.BUY_MARKET,
            volume=2.0,
            intended_price=29_000.00,
            actual_price=29_001.50,
        )
        assert cost == pytest.approx(3.00)

    def test_buy_fill_better_than_intended_is_a_negative_cost(self) -> None:
        cost = log_fill(
            broker="PaperBroker",
            event="open",
            order_id="o2",
            symbol="USTEC",
            order_type=OrderType.BUY_MARKET,
            volume=2.0,
            intended_price=29_000.00,
            actual_price=28_999.00,
        )
        assert cost == pytest.approx(-2.00)

    def test_sell_fill_worse_than_intended_is_a_positive_cost(self) -> None:
        """SELL intended @ 29000.00, actually filled @ 28998.50.

        Received less (adverse), volume 3.0:
        adverse_price_delta = 29000.00 - 28998.50 = 1.50
        realized_slippage_cost = 1.50 * 3.0 = 4.50
        """
        cost = log_fill(
            broker="MT5Broker",
            event="close",
            order_id="o3",
            symbol="USTEC",
            order_type=OrderType.SELL_MARKET,
            volume=3.0,
            intended_price=29_000.00,
            actual_price=28_998.50,
        )
        assert cost == pytest.approx(4.50)

    def test_exact_fill_has_zero_cost(self) -> None:
        cost = log_fill(
            broker="PaperBroker",
            event="open",
            order_id="o4",
            symbol="USTEC",
            order_type=OrderType.BUY_MARKET,
            volume=1.0,
            intended_price=100.0,
            actual_price=100.0,
        )
        assert cost == pytest.approx(0.0)

    def test_none_intended_price_returns_none_and_does_not_raise(self) -> None:
        cost = log_fill(
            broker="MT5Broker",
            event="open",
            order_id="o5",
            symbol="USTEC",
            order_type=OrderType.BUY_MARKET,
            volume=1.0,
            intended_price=None,
            actual_price=100.0,
        )
        assert cost is None


class TestLogFillStructuredOutput:
    """Tests for the actual structured JSON event emitted by log_fill()."""

    def test_emits_a_parseable_json_line_with_all_fields(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.INFO, logger="execution_events"):
            log_fill(
                broker="PaperBroker",
                event="open",
                order_id="abc-123",
                symbol="USTEC",
                order_type=OrderType.BUY_MARKET,
                volume=2.0,
                intended_price=29_000.0,
                actual_price=29_001.5,
            )

        record = next(r for r in caplog.records if r.name == "execution_events")
        payload = cast("dict[str, object]", record.msg)
        assert payload["event_type"] == "fill"
        assert payload["broker"] == "PaperBroker"
        assert payload["fill_event"] == "open"
        assert payload["order_id"] == "abc-123"
        assert payload["symbol"] == "USTEC"
        assert payload["order_type"] == "BUY_MARKET"
        assert payload["volume"] == 2.0
        assert payload["intended_price"] == 29_000.0
        assert payload["actual_price"] == 29_001.5
        assert payload["adverse_price_delta"] == pytest.approx(1.5)
        assert payload["realized_slippage_cost"] == pytest.approx(3.0)

    def test_none_intended_price_logs_null_not_a_missing_key(self) -> None:
        # Directly exercises the formatter to confirm None survives JSON
        # round-tripping as null, rather than log_fill silently omitting
        # the field when there's nothing to compare against.
        record = logging.LogRecord(
            name="execution_events",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg={"intended_price": None, "realized_slippage_cost": None},
            args=None,
            exc_info=None,
        )
        payload = json.loads(JsonFormatter().format(record))
        assert payload["intended_price"] is None
        assert payload["realized_slippage_cost"] is None
