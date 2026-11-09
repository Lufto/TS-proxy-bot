"""Настоящая отправка тревог через aiogram (реализует `services.alerts.AlertSender`)."""

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from tsproxy.services.alerts import AlertButton


class TelegramAlertSender:
    """Шлёт сообщение в тему группы администраторов. Ошибки Telegram ловит `Alerter`."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send(
        self, chat_id: int, thread_id: int | None, text: str, button: AlertButton | None = None
    ) -> None:
        markup = (
            None
            if button is None
            else InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=button.text, callback_data=button.callback_data)]
                ]
            )
        )
        await self._bot.send_message(
            chat_id, text, message_thread_id=thread_id, reply_markup=markup
        )
