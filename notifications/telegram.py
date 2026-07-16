"""Telegram notification dispatcher module.

Sends plain HTTP POST requests directly to the Telegram Bot API
(api.telegram.org) using only the Python standard library (urllib) -- no
external HTTP client or bot framework is used or required for this one-way
"send an alert" use case.
"""

import json
import urllib.error
import urllib.request

from utils.logging import setup_logger

logger = setup_logger("telegram")

TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT_SECONDS = 8.0


class TelegramNotifier:
    """Dispatches text messages to a configured Telegram chat via the Bot API.

    Fails safe in every direction: an unconfigured notifier (empty bot_token
    or chat_id) or any network/HTTP error during send never raises -- it only
    ever returns False, so a missing or misbehaving Telegram integration can
    never crash or block the caller (the same lesson Bug #26 established for
    MT5_LOGIN parsing: a bad/missing credential must degrade the optional
    feature, not the system around it).

    Security note: the Telegram Bot API authenticates via the bot token
    embedded directly in the request URL path (.../bot<TOKEN>/sendMessage) --
    there is no alternate auth mechanism. Because of this, error handling here
    deliberately logs only the exception's type name and (for HTTP errors) the
    status code, never the exception's default string form or the request URL,
    either of which could otherwise leak the token into logs.
    """

    def __init__(
        self, bot_token: str, chat_id: str, timeout: float = DEFAULT_TIMEOUT_SECONDS
    ) -> None:
        """Initializes the TelegramNotifier.

        Args:
            bot_token: Secret bot API token credentials. Empty disables sending.
            chat_id: Destination chat/channel ID parameter. Empty disables sending.
            timeout: HTTP request timeout in seconds (default 8.0) -- keeps a
                Telegram outage or slow network from blocking the caller
                indefinitely.
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    def send_message(self, message: str) -> bool:
        """Dispatches a text message.

        Args:
            message: Formatted text alert contents.

        Returns:
            True if the message was dispatched and Telegram acknowledged it
            (response body has "ok": true). False if the notifier is
            unconfigured (empty bot_token/chat_id), Telegram reported failure,
            or a network/HTTP error occurred. Never raises.
        """
        if not self.bot_token or not self.chat_id:
            logger.warning(
                "Telegram notifier is not configured (empty bot_token/chat_id); skipping send."
            )
            return False

        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        payload = json.dumps({"chat_id": self.chat_id, "text": message}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Log only the status code -- never exc's default str()/repr() or
            # the request URL, both of which embed the bot token.
            logger.warning("Telegram send failed: HTTPError, status=%d", exc.code)
            return False
        except urllib.error.URLError as exc:
            # exc.reason is a plain exception/string (e.g. a timeout or DNS
            # failure), never the URL itself.
            logger.warning("Telegram send failed: URLError (%s)", type(exc.reason).__name__)
            return False
        except Exception as exc:  # noqa: BLE001 - last-resort safety net, must never propagate
            logger.warning("Telegram send failed: %s", type(exc).__name__)
            return False

        if body.get("ok") is True:
            return True

        logger.warning("Telegram API reported failure (ok=false).")
        return False
