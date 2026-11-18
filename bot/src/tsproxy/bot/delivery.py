"""Доставка сообщений клиенту: живое меню и пометка «клиент заблокировал бота».

Все исходящие сообщения клиенту идут через `ClientDelivery`: если Telegram ответил
«бот заблокирован» (`TelegramForbiddenError`), бот ставит клиенту пометку и дальше
не шлёт ему уведомления и рассылки. Пометку снимает новый `/start`.

Живое меню (спека «Внешний вид бота», задача 23). Нажатие кнопки — `show`: экран
приходит новым сообщением (Telegram поднимает чат в списке только от нового сообщения),
нажатое сообщение бот убирает (`remove`):
- удаляет по одному сообщению (`deleteMessage`), не пакетом: из-за одного сообщения
  старше 48 часов Telegram отказывает всему пакету;
- удалить нельзя (`message can't be deleted…` — старше 48 часов, `message to delete not
  found` — уже удалено) — снимает кнопки; «кнопок уже нет» (`message is not modified`)
  и «сообщения нет» при снятии — не ошибка;
- новый экран не дошёл (клиент заблокировал бота) — нажатое не трогаем.

Виды сообщений:
- экран меню (ответ на кнопку или команду) — при нажатии удаляется;
- сообщение с сохраняемым текстом (`Screen.keeps_text`: уведомления, сообщения об
  оплате и возврате, льготный период, компенсация, ручная выдача) — при нажатии теряет
  только кнопки. Бот узнаёт его по метке `KEPT_TEXT_MARK` в данных кнопок: Telegram
  присылает кнопки нажатого сообщения, так что память не нужна и перезапуск не мешает;
- сообщение без кнопок (переписка с поддержкой, чеки) — бот его не касается.

Отправленные до метки уведомления при нажатии удалятся как экран меню.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    TelegramObject,
)

from tsproxy.bot.screens import Screen
from tsproxy.services.clients import ClientService

logger = logging.getLogger(__name__)

# Ответы Telegram на «удалить сообщение», после которых у сообщения только снимаем кнопки
MESSAGE_NOT_DELETABLE = (
    "message can't be deleted",  # старше 48 часов («… for everyone») или служебное
    "message to delete not found",  # клиент уже удалил
)
MESSAGE_NOT_MODIFIED = "message is not modified"  # правка ничего не меняет
# Ответы Telegram на «снять кнопки», которые не ошибка бота
BUTTONS_ALREADY_GONE = (
    MESSAGE_NOT_MODIFIED,  # кнопок уже нет
    "message to edit not found",  # сообщения уже нет
)


# Метка в данных кнопок сообщения с сохраняемым текстом (`Screen.keeps_text`). Telegram
# присылает кнопки нажатого сообщения вместе с нажатием, поэтому по метке бот без памяти
# и после перезапуска знает, что сообщение нельзя удалять. До обработчиков метку снимает
# `KeptTextMiddleware`. Доступа метка не даёт: подделанная только сохранит меню.
KEPT_TEXT_MARK = "~"
CALLBACK_DATA_MAX_BYTES = 64  # предел Telegram для данных кнопки


def _mark_button(button: InlineKeyboardButton) -> InlineKeyboardButton:
    if button.callback_data is None:
        return button  # ссылка, «скопировать» — нажатие не приходит боту
    data = KEPT_TEXT_MARK + button.callback_data
    if len(data.encode()) > CALLBACK_DATA_MAX_BYTES:
        raise ValueError(f"Данные кнопки с меткой длиннее {CALLBACK_DATA_MAX_BYTES} байт: {data!r}")
    return button.model_copy(update={"callback_data": data})


def _kept_markup(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Кнопки сообщения с сохраняемым текстом — с меткой в данных."""
    rows = [[_mark_button(button) for button in row] for row in markup.inline_keyboard]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def keeps_text(message: Message) -> bool:
    """Нажатое сообщение — с сохраняемым текстом: его кнопки несут метку."""
    markup = message.reply_markup
    if markup is None:
        return False
    return any(
        (button.callback_data or "").startswith(KEPT_TEXT_MARK)
        for row in markup.inline_keyboard
        for button in row
    )


class KeptTextMiddleware(BaseMiddleware):
    """Снимает метку `KEPT_TEXT_MARK` с данных нажатой кнопки: обработчики и фильтры
    видят те же данные, что у кнопки обычного экрана."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery) and (event.data or "").startswith(KEPT_TEXT_MARK):
            data_without_mark = (event.data or "").removeprefix(KEPT_TEXT_MARK)
            event = event.model_copy(update={"data": data_without_mark})
        return await handler(event, data)


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
    # кнопки под счётом: первой Telegram требует кнопку оплаты (`pay=True`)
    markup: InlineKeyboardMarkup | None = None


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
        markup = _kept_markup(screen.markup) if screen.keeps_text else screen.markup
        return await self.call(
            telegram_id, self._bot.send_message(telegram_id, screen.text, reply_markup=markup)
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
                reply_markup=invoice.markup,
            ),
        )

    async def show(self, callback: CallbackQuery, screen: Screen) -> None:
        """Живое меню: экран — новым сообщением, нажатое сообщение убрать (`remove`).

        Нажатое сообщение с сохраняемым текстом (`keeps_text`) только теряет кнопки.
        """
        telegram_id = callback.from_user.id
        if await self.send(telegram_id, screen) is None:
            return  # клиент заблокировал бота — убирать нечего
        pressed = callback.message
        if not isinstance(pressed, Message):
            return  # Telegram не прислал сообщение: оно недоступно боту
        if keeps_text(pressed):
            await self._strip_buttons(telegram_id, pressed)
        else:
            await self.remove(telegram_id, pressed)

    async def remove(self, telegram_id: int, message: Message) -> None:
        """Убрать прошлое меню: удалить, а если Telegram не даёт — снять кнопки.

        Удаляем по одному сообщению, не пакетом (`deleteMessages`): из-за одного сообщения
        старше 48 часов Telegram отказывает всему пакету.
        """
        try:
            await self.call(telegram_id, message.delete())
        except TelegramBadRequest as error:
            if not any(reason in error.message for reason in MESSAGE_NOT_DELETABLE):
                raise
            logger.info("Сообщение %s не удалить (%s), снимаем кнопки", message.message_id, error)
            await self._strip_buttons(telegram_id, message)

    async def _strip_buttons(self, telegram_id: int, message: Message) -> None:
        """Снять кнопки, текст оставить. Срока у этой правки для своих сообщений нет."""
        try:
            await self.call(telegram_id, message.edit_reply_markup(reply_markup=None))
        except TelegramBadRequest as error:
            if not any(reason in error.message for reason in BUTTONS_ALREADY_GONE):
                raise
