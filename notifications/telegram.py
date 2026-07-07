"""Telegram notification dispatcher module."""


class TelegramNotifier:
    """Dispatches text messages and charts directly to a configured Telegram chat."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        """Initializes the TelegramNotifier.

        Args:
            bot_token: Secret bot API token credentials.
            chat_id: Destination chat/channel ID parameter.
        """
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send_message(self, message: str) -> bool:
        """Dispatches a text message.

        Args:
            message: Formatted text alert contents.

        Returns:
            True if message dispatched successfully, False otherwise.
        """
        raise NotImplementedError(
            "Telegram message dispatch logic will be implemented in a future sprint."
        )
