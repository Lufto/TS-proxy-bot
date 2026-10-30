"""Отправка сообщений клиенту с пометкой «клиент заблокировал бота».

Все исходящие сообщения клиенту идут через `ClientDelivery`: если Telegram ответил
«бот заблокирован» (`TelegramForbiddenError`), бот ставит клиенту пометку и дальше
не шлёт ему уведомления и рассылки. Пометку снимает новый `/start`.
"""

import logging
from collections.abc import Awaitable
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import CallbackQuery, LabeledPrice, Message

from tsproxy.bot.screens import Screen
from tsproxy.services.clients import ClientService

logger = logging.getLogger(__name__)

# Ответы Telegram на «изменить сообщение», которые не ошибка бота
MESSAGE_NOT_MODIFIED = "message is not modified"  # нажали ту же кнопку ещё раз
MESSAGE_NOT_EDITABLE = (
    "message can't be edited",  # сообщение слишком старое
    "message to edit not found",  # клиент удалил сообщение
)


@dataclass(frozen=True)
class Invoice:
    """Счёт Telegram: заголовок до 32 символов, описание до 255, сумма — в единицах валюты
    (у звёзд — целые звёзды)."""

    title: str
    description: str
    payload: str
    currency: str
    label: str
    amount: int


class ClientDelivery:
    """Исходящие запросы к Telegram от имени клиента."""

    def __init__(self, bot: Bot, clients: ClientService) -> None:
        self._bot = bot
        self._clients = clients

    async def call[T](self, telegram_id: int, request: Awaitable[T]) -> T | None:
        """Выполнить запрос к Telegram для клиента. `None` — клиент заблокировал бота."""
        try:
            return await request
        except TelegramForbiddenError as error:
            # Ожидаемая ситуация: клиент заблокировал бота или удалил аккаунт Telegram
            logger.info("Telegram не доставил сообщение клиенту %s: %s", telegram_id, error)
            await self._clients.mark_blocked(telegram_id)
            return None

    async def send(self, telegram_id: int, screen: Screen) -> Message | None:
        """Новое сообщение клиенту. `None` — клиент заблокировал бота."""
        return await self.call(
            telegram_id,
            self._bot.send_message(telegram_id, screen.text, reply_markup=screen.markup),
        )

    async def invoice(self, telegram_id: int, invoice: Invoice) -> Message | None:
        """Счёт в звёздах Telegram (задача 33): сообщение с кнопкой оплаты.

        `provider_token` пустой — так Telegram требует для звёзд. `None` — клиент
        заблокировал бота.
        """
        return await self.call(
            telegram_id,
            self._bot.send_invoice(
                telegram_id,
                title=invoice.title,
                description=invoice.description,
                payload=invoice.payload,
                currency=invoice.currency,
                prices=[LabeledPrice(label=invoice.label, amount=invoice.amount)],
                provider_token="",
            ),
        )

    async def show(self, callback: CallbackQuery, screen: Screen) -> None:
        """Показать экран в том же сообщении, где нажата кнопка (меню — одно сообщение).

        Если сообщение изменить нельзя (слишком старое или удалено) — прислать новое.
        """
        telegram_id = callback.from_user.id
        message = callback.message
        if isinstance(message, Message) and await self._edit(telegram_id, message, screen):
            return
        await self.send(telegram_id, screen)

    async def _edit(self, telegram_id: int, message: Message, screen: Screen) -> bool:
        """`False` — изменить не получилось, нужно новое сообщение."""
        try:
            await self.call(telegram_id, message.edit_text(screen.text, reply_markup=screen.markup))
        except TelegramBadRequest as error:
            if MESSAGE_NOT_MODIFIED in error.message:
                return True  # на экране уже то, что нужно
            if any(reason in error.message for reason in MESSAGE_NOT_EDITABLE):
                logger.info("Сообщение %s не изменить, шлём новое", message.message_id)
                return False
            raise
        return True
