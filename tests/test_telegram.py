"""Unit tests for notifications/telegram.py (TelegramNotifier).

No real HTTP request is made anywhere in this file -- urllib.request.urlopen
is always mocked.
"""

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from notifications.telegram import TelegramNotifier

FAKE_TOKEN = "123456789:AAFakeSecretTokenValueForTestingOnly-XYZ"
FAKE_CHAT_ID = "-1001234567890"


def _fake_response(body: dict) -> MagicMock:
    """Builds a fake urlopen() context-manager response yielding a JSON body."""
    response = MagicMock()
    response.read.return_value = json.dumps(body).encode("utf-8")
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    return response


class TestUnconfiguredNotifier:
    """Empty credentials must disable sending entirely -- no HTTP call attempted."""

    def test_empty_bot_token_returns_false_without_network_call(self) -> None:
        notifier = TelegramNotifier(bot_token="", chat_id=FAKE_CHAT_ID)
        with patch("notifications.telegram.urllib.request.urlopen") as mock_urlopen:
            result = notifier.send_message("test message")

        assert result is False
        mock_urlopen.assert_not_called()

    def test_empty_chat_id_returns_false_without_network_call(self) -> None:
        notifier = TelegramNotifier(bot_token=FAKE_TOKEN, chat_id="")
        with patch("notifications.telegram.urllib.request.urlopen") as mock_urlopen:
            result = notifier.send_message("test message")

        assert result is False
        mock_urlopen.assert_not_called()

    def test_both_empty_returns_false_without_network_call(self) -> None:
        notifier = TelegramNotifier(bot_token="", chat_id="")
        with patch("notifications.telegram.urllib.request.urlopen") as mock_urlopen:
            result = notifier.send_message("test message")

        assert result is False
        mock_urlopen.assert_not_called()


class TestSuccessfulSend:
    def test_send_message_returns_true_on_ok_response(self) -> None:
        notifier = TelegramNotifier(bot_token=FAKE_TOKEN, chat_id=FAKE_CHAT_ID)
        with patch(
            "notifications.telegram.urllib.request.urlopen",
            return_value=_fake_response({"ok": True, "result": {"message_id": 42}}),
        ) as mock_urlopen:
            result = notifier.send_message("MIDLINE SWEEP SIGNAL -- USTEC")

        assert result is True
        request = mock_urlopen.call_args[0][0]
        assert request.full_url == f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage"
        assert request.get_method() == "POST"
        sent_body = json.loads(request.data.decode("utf-8"))
        assert sent_body == {"chat_id": FAKE_CHAT_ID, "text": "MIDLINE SWEEP SIGNAL -- USTEC"}

    def test_send_message_uses_configured_timeout(self) -> None:
        notifier = TelegramNotifier(bot_token=FAKE_TOKEN, chat_id=FAKE_CHAT_ID, timeout=3.5)
        with patch(
            "notifications.telegram.urllib.request.urlopen",
            return_value=_fake_response({"ok": True}),
        ) as mock_urlopen:
            notifier.send_message("test")

        assert mock_urlopen.call_args.kwargs["timeout"] == 3.5

    def test_send_message_returns_false_when_api_reports_not_ok(self) -> None:
        notifier = TelegramNotifier(bot_token=FAKE_TOKEN, chat_id=FAKE_CHAT_ID)
        with patch(
            "notifications.telegram.urllib.request.urlopen",
            return_value=_fake_response({"ok": False, "description": "chat not found"}),
        ):
            result = notifier.send_message("test")

        assert result is False


class TestSanitizedErrorLogging:
    """The bot token lives in the request URL -- error handling must never log
    the raw exception or the URL, only a sanitized type name/status code.
    """

    def test_http_error_returns_false_and_never_logs_token_or_url(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        notifier = TelegramNotifier(bot_token=FAKE_TOKEN, chat_id=FAKE_CHAT_ID)
        url = f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage"
        http_error = urllib.error.HTTPError(url, 401, "Unauthorized", hdrs=None, fp=None)  # type: ignore[arg-type]

        with patch("notifications.telegram.urllib.request.urlopen", side_effect=http_error):
            with caplog.at_level("WARNING"):
                result = notifier.send_message("test")

        assert result is False
        assert FAKE_TOKEN not in caplog.text
        assert url not in caplog.text
        assert "401" in caplog.text
        assert "HTTPError" in caplog.text

    def test_url_error_returns_false_and_never_logs_token_or_url(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        notifier = TelegramNotifier(bot_token=FAKE_TOKEN, chat_id=FAKE_CHAT_ID)
        url_error = urllib.error.URLError(TimeoutError("timed out"))

        with patch("notifications.telegram.urllib.request.urlopen", side_effect=url_error):
            with caplog.at_level("WARNING"):
                result = notifier.send_message("test")

        assert result is False
        assert FAKE_TOKEN not in caplog.text
        assert "URLError" in caplog.text

    def test_unexpected_exception_is_caught_and_never_logs_token(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A malformed/non-JSON response (or any other unexpected failure) must
        still return False rather than propagate, and must not leak the token.
        """
        notifier = TelegramNotifier(bot_token=FAKE_TOKEN, chat_id=FAKE_CHAT_ID)
        broken_response = MagicMock()
        broken_response.read.return_value = b"not valid json"
        broken_response.__enter__.return_value = broken_response
        broken_response.__exit__.return_value = False

        with patch("notifications.telegram.urllib.request.urlopen", return_value=broken_response):
            with caplog.at_level("WARNING"):
                result = notifier.send_message("test")

        assert result is False
        assert FAKE_TOKEN not in caplog.text

    def test_unconfigured_warning_does_not_log_empty_credentials_as_if_valid(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        notifier = TelegramNotifier(bot_token="", chat_id="")
        with caplog.at_level("WARNING"):
            result = notifier.send_message("test")

        assert result is False
        assert "not configured" in caplog.text
