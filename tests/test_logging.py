"""Unit tests for utils/logging.py's structured (JSON-line) logging support.

Sprint 6c, T2 -- setup_logger() itself is unchanged, not re-tested here.
"""

import json
import logging
from collections.abc import Generator
from pathlib import Path

import pytest

from utils.logging import JsonFormatter, setup_structured_logger


def _make_record(msg: object, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name="test_logger",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=None,
        exc_info=None,
    )


class TestJsonFormatter:
    """Tests for JsonFormatter."""

    def test_dict_message_fields_are_merged_into_the_payload(self) -> None:
        record = _make_record({"event_type": "fill", "order_id": "abc123", "price": 100.5})
        formatted = JsonFormatter().format(record)
        payload = json.loads(formatted)

        assert payload["event_type"] == "fill"
        assert payload["order_id"] == "abc123"
        assert payload["price"] == 100.5

    def test_output_includes_timestamp_level_and_logger_name(self) -> None:
        record = _make_record({"event_type": "fill"})
        payload = json.loads(JsonFormatter().format(record))

        assert "timestamp" in payload
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test_logger"

    def test_string_message_is_wrapped_under_event_key(self) -> None:
        record = _make_record("a plain string message")
        payload = json.loads(JsonFormatter().format(record))

        assert payload["event"] == "a plain string message"

    def test_output_is_valid_single_line_json(self) -> None:
        record = _make_record({"event_type": "fill", "nested": {"a": 1}})
        formatted = JsonFormatter().format(record)

        assert "\n" not in formatted
        payload = json.loads(formatted)  # must not raise
        assert payload["nested"] == {"a": 1}

    def test_non_json_native_values_are_stringified_not_raising(self) -> None:
        class Unserializable:
            def __str__(self) -> str:
                return "<unserializable>"

        record = _make_record({"event_type": "fill", "weird": Unserializable()})
        payload = json.loads(JsonFormatter().format(record))  # must not raise

        assert payload["weird"] == "<unserializable>"


class TestSetupStructuredLogger:
    """Tests for setup_structured_logger()."""

    def test_returns_a_logger_with_json_formatter_handlers(self) -> None:
        logger = setup_structured_logger("test_structured_unique_1", log_to_file=False)
        assert logger.handlers
        assert all(isinstance(h.formatter, JsonFormatter) for h in logger.handlers)

    def test_repeated_calls_with_same_name_do_not_duplicate_handlers(self) -> None:
        logger1 = setup_structured_logger("test_structured_unique_2", log_to_file=False)
        handler_count = len(logger1.handlers)
        logger2 = setup_structured_logger("test_structured_unique_2", log_to_file=False)

        assert logger1 is logger2
        assert len(logger2.handlers) == handler_count

    def test_log_to_file_true_creates_a_log_file(self, tmp_path: Path) -> None:
        logger = setup_structured_logger(
            "test_structured_unique_3", log_to_file=True, log_dir=str(tmp_path)
        )
        logger.info({"event_type": "fill", "order_id": "xyz"})

        log_file = tmp_path / "test_structured_unique_3.log"
        assert log_file.exists()
        line = log_file.read_text().strip().splitlines()[-1]
        payload = json.loads(line)
        assert payload["order_id"] == "xyz"

    def test_does_not_affect_human_readable_setup_logger(self) -> None:
        from utils.logging import setup_logger

        text_logger = setup_logger("test_text_unique_1")
        structured_logger = setup_structured_logger("test_structured_unique_4", log_to_file=False)

        assert text_logger is not structured_logger
        assert not any(isinstance(h.formatter, JsonFormatter) for h in text_logger.handlers)


@pytest.fixture(autouse=True)
def _cleanup_test_loggers() -> Generator[None, None, None]:
    """Removes handlers from the uniquely-named test loggers after each test.

    So tmp_path-based FileHandlers (test_log_to_file_true_creates_a_log_file)
    don't keep a file handle open into a deleted tmp_path across test runs.
    """
    yield
    for name in (
        "test_structured_unique_1",
        "test_structured_unique_2",
        "test_structured_unique_3",
        "test_structured_unique_4",
        "test_text_unique_1",
    ):
        logger = logging.getLogger(name)
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)
