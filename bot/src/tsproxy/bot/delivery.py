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

Экран не по нажатию (`/start`, команды, уведомления — `send`) тоже убирает прошлое живое
меню (задача 24). Telegram не даёт узнать последнее сообщение чата, поэтому бот помнит
последнее живое меню каждого клиента в памяти (база не меняется): живое меню — последнее
присланное сообщение с кнопками, нажатие которых приходит боту. После перезапуска память
пуста, и у клиента может остаться одно старое меню с рабочими кнопками — оно уберётся при
нажатии на нём; при этом убирается и меню, которое бот помнит (`show`).

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
# Ответ на нажатие опоздал (бот долго ждал панель или платёжку) — Telegram его уже не примет
QUERY_TOO_OLD = "query is too old"
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


def kept_markup(markup: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    """Кнопки сообщения с сохраняемым текстом — с меткой в данных. Для сообщений, которые
    уходят мимо `send` (ответы поддержки: копия с подписью), метку ставят сами."""
    rows = [[_mark_button(button) for button in row] for row in markup.inline_keyboard]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def has_pressable_buttons(markup: InlineKeyboardMarkup) -> bool:
    """Есть кнопки, нажатие которых приходит боту (ссылки и «скопировать» не в счёт)."""
    return any(button.callback_data for row in markup.inline_keyboard for button in row)


@dataclass(frozen=True)
class LiveMenu:
    """Сообщение бота с кнопками, которое убирается при новом живом меню."""

    message_id: int
    keeps_text: bool  # сохраняемый текст — снять только кнопки


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
        # Последнее живое меню каждого клиента: Telegram не даёт узнать последнее
        # сообщение чата, номер известен только при отправке. После перезапуска пусто.
        self._live_menus: dict[int, LiveMenu] = {}

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
        """Экран не по нажатию (команда, уведомление): новым сообщением, прошлое живое
        меню, которое бот помнит, убрать. `None` — клиент заблокировал бота."""
        sent = await self._send(telegram_id, screen)
        if sent is not None:
            previous = self._remember(telegram_id, sent, screen)
            if previous is not None:
                await self._take_down(telegram_id, previous)
        return sent

    async def _send(self, telegram_id: int, screen: Screen) -> Message | None:
        markup = kept_markup(screen.markup) if screen.keeps_text else screen.markup
        return await self.call(
            telegram_id, self._bot.send_message(telegram_id, screen.text, reply_markup=markup)
        )

    def _remember(self, telegram_id: int, sent: Message, screen: Screen) -> LiveMenu | None:
        """Запомнить новое живое меню (если у него есть кнопки, которые нажимают) и вернуть
        прошлое. Без ожиданий внутри: два экрана подряд не запомнят одно прошлое дважды."""
        previous = self._live_menus.pop(telegram_id, None)
        if has_pressable_buttons(screen.markup):
            self._live_menus[telegram_id] = LiveMenu(sent.message_id, screen.keeps_text)
        return previous

    async def _take_down(self, telegram_id: int, menu: LiveMenu) -> None:
        """Убрать прошлое живое меню: сохраняемый текст теряет только кнопки."""
        if menu.keeps_text:
            await self._strip_buttons(telegram_id, menu.message_id)
        else:
            await self.remove(telegram_id, menu.message_id)

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

    async def answer(self, callback: CallbackQuery, *, alert: str | None = None) -> None:
        """Ответить на нажатие. `alert` — короткий ответ (до 200 символов) всплывашкой над
        кнопкой вместо отдельного экрана.

        Опоздавший ответ (`query is too old`) не ошибка: экран после него всё равно показываем.
        """
        try:
            await callback.answer(alert, show_alert=alert is not None)
        except TelegramBadRequest as error:
            if QUERY_TOO_OLD not in error.message:
                raise
            logger.info("Ответ на нажатие клиента %s опоздал: %s", callback.from_user.id, error)

    async def show(self, callback: CallbackQuery, screen: Screen) -> None:
        """Живое меню: экран — новым сообщением, нажатое сообщение убрать (`remove`).

        Нажатое сообщение с сохраняемым текстом (`keeps_text`) только теряет кнопки.
        Живое меню, которое бот помнит, тоже убирается, если нажали не на нём (старое
        меню, оставшееся с прошлого запуска).
        """
        telegram_id = callback.from_user.id
        sent = await self._send(telegram_id, screen)
        if sent is None:
            return  # клиент заблокировал бота — убирать нечего
        previous = self._remember(telegram_id, sent, screen)
        pressed = callback.message
        pressed_id = None
        if isinstance(pressed, Message):  # иначе Telegram не прислал: сообщение недоступно
            pressed_id = pressed.message_id
            await self._take_down(telegram_id, LiveMenu(pressed_id, keeps_text(pressed)))
        if previous is not None and previous.message_id != pressed_id:
            await self._take_down(telegram_id, previous)

    async def remove(self, telegram_id: int, message_id: int) -> None:
        """Убрать прошлое меню: удалить, а если Telegram не даёт — снять кнопки.

        Удаляем по одному сообщению, не пакетом (`deleteMessages`): из-за одного сообщения
        старше 48 часов Telegram отказывает всему пакету.
        """
        try:
            await self.call(telegram_id, self._bot.delete_message(telegram_id, message_id))
        except TelegramBadRequest as error:
            if not any(reason in error.message for reason in MESSAGE_NOT_DELETABLE):
                raise
            logger.info("Сообщение %s не удалить (%s), снимаем кнопки", message_id, error)
            await self._strip_buttons(telegram_id, message_id)

    async def _strip_buttons(self, telegram_id: int, message_id: int) -> None:
        """Снять кнопки, текст оставить. Срока у этой правки для своих сообщений нет."""
        try:
            await self.call(
                telegram_id,
                self._bot.edit_message_reply_markup(
                    chat_id=telegram_id, message_id=message_id, reply_markup=None
                ),
            )
        except TelegramBadRequest as error:
            if not any(reason in error.message for reason in BUTTONS_ALREADY_GONE):
                raise
