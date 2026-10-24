"""Настоящая отправка тревог через aiogram (реализует `services.alerts.AlertSender`)."""

from aiogram import Bot


class TelegramAlertSender:
    """Шлёт сообщение в тему группы администраторов. Ошибки Telegram ловит `Alerter`."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(self, chat_id: int, thread_id: int | None, text: str) -> None:
        await self._bot.send_message(chat_id, text, message_thread_id=thread_id)
