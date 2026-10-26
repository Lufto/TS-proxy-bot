"""`/broadcast` в группе администраторов (задача 19): рассылка с предпросмотром и подтверждением.

1. `/broadcast <текст>` — бот присылает шапку (сколько получат) и предпросмотр: текст рассылки
   ровно так, как его увидят клиенты, с кнопками «✅ Отправить» и «✖️ Отмена».
   Форматирование администратора (жирный, ссылки) переводится в HTML из разметки Telegram.
2. «✅ Отправить» — текст берётся из самого сообщения предпросмотра (`html_text`) и уходит
   с тем же режимом разметки (HTML), что и предпросмотр: клиенты видят то же, что видел
   администратор. Ничего не дописываем и не экранируем молча.
3. Ход и итог рассылки — в ту же тему; под «Рассылка началась» — «⏹ Остановить».

Повторное «✅ Отправить», второй администратор, отправка после «Отмены» — `BroadcastService`
находит запись в журнале и ничего не шлёт. Права — `AdminFilter` на каждом обработчике.
"""

import logging
import math
from enum import StrEnum

from aiogram import Bot, Router
from aiogram.enums import MessageEntityType, ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.utils.text_decorations import add_surrogates, html_decoration, remove_surrogates

from tsproxy.bot.admin import AdminAccess, AdminFilter
from tsproxy.bot.admin_clients import on_foreign_press
from tsproxy.bot.admin_stats import format_duration
from tsproxy.bot.context import BotContext
from tsproxy.bot.delivery import MESSAGE_NOT_MODIFIED, ClientDelivery
from tsproxy.content.loader import Texts
from tsproxy.services.broadcast import (
    BROADCAST_SEND_INTERVAL,
    BroadcastEnd,
    BroadcastProgress,
    BroadcastReport,
    BroadcastService,
    BroadcastThrottled,
    DeliveryOutcome,
    PreviewRef,
    StartOutcome,
)

logger = logging.getLogger(__name__)

BROADCAST_COMMAND = "broadcast"
UTF16_UNIT_BYTES = 2  # смещения разметки Telegram — в единицах UTF-16
SECONDS_PER_MINUTE = 60
LINE = "\n"

END_TEXTS: dict[BroadcastEnd, str] = {
    BroadcastEnd.COMPLETED: "admin.broadcast.completed",
    BroadcastEnd.STOPPED: "admin.broadcast.stopped",
    BroadcastEnd.ABORTED: "admin.broadcast.aborted",
    BroadcastEnd.CRASHED: "admin.broadcast.crashed",
}
START_REFUSALS: dict[StartOutcome, str] = {
    StartOutcome.ALREADY_HANDLED: "admin.broadcast.already_handled",
    StartOutcome.BUSY: "admin.broadcast.busy",
    StartOutcome.NO_RECIPIENTS: "admin.broadcast.no_recipients",
}


class BroadcastAction(StrEnum):
    SEND = "send"
    CANCEL = "cancel"
    STOP = "stop"


class AdminBroadcastCallback(CallbackData, prefix="admbc"):
    """Кнопка рассылки. Что отправлять — берётся из сообщения предпросмотра, не из кнопки."""

    action: BroadcastAction


def _utf16_length(text: str) -> int:
    return len(add_surrogates(text)) // UTF16_UNIT_BYTES


def command_body_html(message: Message) -> str | None:
    """Текст после команды как HTML, с разметкой администратора. `None` — текста нет.

    Разметку Telegram (жирный, ссылки, …) сдвигаем на длину команды и пробелов после неё
    и переводим в HTML; обычный текст экранируется — клиенты увидят ровно написанное.
    """
    text = message.text
    entities = message.entities or []
    command = next(
        (e for e in entities if e.type == MessageEntityType.BOT_COMMAND and e.offset == 0), None
    )
    if text is None or command is None:
        return None
    rest = remove_surrogates(add_surrogates(text)[command.length * UTF16_UNIT_BYTES :])
    body = rest.lstrip()
    if not body.strip():
        return None
    shift = command.length + _utf16_length(rest[: len(rest) - len(body)])
    moved = []
    for entity in entities:
        end = entity.offset + entity.length
        if entity is command or end <= shift:
            continue
        start = max(entity.offset, shift)
        moved.append(entity.model_copy(update={"offset": start - shift, "length": end - start}))
    return html_decoration.unparse(body, moved)


# --- Отправка и отчёты ---


class TelegramBroadcastSender:
    """Отправка одного сообщения рассылки (реализует `services.broadcast.BroadcastSender`).

    Заблокировавших помечает `ClientDelivery` — им больше не шлём ни рассылок, ни уведомлений.
    """

    def __init__(self, bot: Bot, delivery: ClientDelivery) -> None:
        self._bot = bot
        self._delivery = delivery

    async def send(self, telegram_id: int, text: str) -> DeliveryOutcome:
        request = self._bot.send_message(telegram_id, text, parse_mode=ParseMode.HTML)
        try:
            sent = await self._delivery.call(telegram_id, request)
        except TelegramRetryAfter as error:
            raise BroadcastThrottled(error.retry_after) from error
        except (TelegramBadRequest, TelegramNetworkError, TelegramServerError) as error:
            # Один клиент не получил — рассылка идёт дальше; неудачи считает сервис
            logger.warning("Рассылка: клиенту %s не отправлено: %s", telegram_id, error)
            return DeliveryOutcome.FAILED
        return DeliveryOutcome.BLOCKED if sent is None else DeliveryOutcome.DELIVERED


def stop_markup(texts: Texts) -> InlineKeyboardMarkup:
    button = InlineKeyboardButton(
        text=texts.get("admin.broadcast.stop_button"),
        callback_data=AdminBroadcastCallback(action=BroadcastAction.STOP).pack(),
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


def _counts(texts: Texts, progress: BroadcastProgress) -> str:
    return texts.get(
        "admin.broadcast.counts",
        delivered=progress.delivered,
        total=progress.total,
        blocked=progress.blocked,
        failed=progress.failed,
    )


def report_text(texts: Texts, report: BroadcastReport) -> str:
    key = END_TEXTS[report.end]
    if report.end is BroadcastEnd.COMPLETED:
        head = texts.get(
            key, duration=format_duration(texts, report.finished_at - report.started_at)
        )
    else:
        head = texts.get(key)
    return LINE.join([head, _counts(texts, report.progress)])


def estimated_minutes(total: int) -> int:
    """Сколько минут займёт рассылка в нашем темпе (вверх, не меньше минуты)."""
    seconds = total * BROADCAST_SEND_INTERVAL.total_seconds()
    return max(1, math.ceil(seconds / SECONDS_PER_MINUTE))


class TelegramBroadcastReporter:
    """Ход и итог рассылки — в тему группы администраторов, где нажали «✅ Отправить»."""

    def __init__(self, bot: Bot, texts: Texts, chat_id: int, thread_id: int | None) -> None:
        self._bot = bot
        self._texts = texts
        self._chat_id = chat_id
        self._thread_id = thread_id

    async def started(self, total: int) -> None:
        text = self._texts.get(
            "admin.broadcast.started", total=total, minutes=estimated_minutes(total)
        )
        await self._post(text, stop_markup(self._texts))

    async def progress(self, progress: BroadcastProgress) -> None:
        await self._post(
            self._texts.get(
                "admin.broadcast.progress", processed=progress.processed, total=progress.total
            )
        )

    async def finished(self, report: BroadcastReport) -> None:
        await self._post(report_text(self._texts, report))

    async def _post(self, text: str, markup: InlineKeyboardMarkup | None = None) -> None:
        try:
            await self._bot.send_message(
                self._chat_id, text, message_thread_id=self._thread_id, reply_markup=markup
            )
        # Ловим всё: сбой отчёта не должен останавливать рассылку, а ошибки Telegram и сети
        # бывают разных типов. Ошибку не прячем — пишем в лог с трассировкой.
        except Exception:
            logger.exception("Не удалось отправить отчёт о рассылке в тему %s", self._thread_id)


# --- Обработчики ---


def confirm_markup(texts: Texts) -> InlineKeyboardMarkup:
    def button(key: str, action: BroadcastAction) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=texts.get(key), callback_data=AdminBroadcastCallback(action=action).pack()
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button("admin.broadcast.send_button", BroadcastAction.SEND),
                button("admin.broadcast.cancel_button", BroadcastAction.CANCEL),
            ]
        ]
    )


async def _remove_buttons(message: Message) -> None:
    """Убрать кнопки, чтобы их не нажали ещё раз. Уже убраны — не ошибка."""
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as error:
        if MESSAGE_NOT_MODIFIED not in error.message:
            raise


class AdminBroadcastHandlers:
    """Команда `/broadcast` и кнопки «✅ Отправить», «✖️ Отмена», «⏹ Остановить»."""

    def __init__(self, broadcasts: BroadcastService) -> None:
        self._broadcasts = broadcasts

    async def on_command(self, message: Message, ctx: BotContext) -> None:
        texts = ctx.admin_texts
        body = command_body_html(message)
        if body is None:
            await message.answer(texts.get("admin.broadcast.usage"))
            return
        audience = await self._broadcasts.audience()
        await message.answer(
            texts.get(
                "admin.broadcast.preview_header",
                recipients=audience.recipients,
                blocked=audience.blocked,
            )
        )
        try:
            await message.answer(
                body, parse_mode=ParseMode.HTML, reply_markup=confirm_markup(texts)
            )
        except TelegramBadRequest as error:
            # Например, текст длиннее предела Telegram: предпросмотра нет — отправить нечего
            logger.info("Предпросмотр рассылки не принят Telegram: %s", error.message)
            await message.answer(texts.get("admin.broadcast.preview_failed", error=error.message))

    async def on_press(
        self,
        callback: CallbackQuery,
        callback_data: AdminBroadcastCallback,
        bot: Bot,
        ctx: BotContext,
    ) -> None:
        texts = ctx.admin_texts
        message = callback.message
        if not isinstance(message, Message):
            # Сообщение старше 48 часов: Telegram его не отдаёт, текста предпросмотра нет
            await callback.answer(texts.get("admin.broadcast.preview_expired"), show_alert=True)
            return
        admin_id = callback.from_user.id
        match callback_data.action:
            case BroadcastAction.SEND:
                await self._send(callback, message, bot, texts)
            case BroadcastAction.CANCEL:
                preview = PreviewRef(message.chat.id, message.message_id)
                cancelled = await self._broadcasts.cancel(admin_id, preview)
                key = (
                    "admin.broadcast.cancelled" if cancelled else "admin.broadcast.already_handled"
                )
                await callback.answer(texts.get(key), show_alert=not cancelled)
                await _remove_buttons(message)
            case BroadcastAction.STOP:
                stopping = self._broadcasts.running
                await callback.answer(
                    texts.get(
                        "admin.broadcast.stopping" if stopping else "admin.broadcast.not_running"
                    )
                )
                await _remove_buttons(message)
                await self._broadcasts.stop(admin_id)

    async def _send(
        self, callback: CallbackQuery, message: Message, bot: Bot, texts: Texts
    ) -> None:
        if not message.text:
            await callback.answer(texts.get("admin.broadcast.preview_expired"), show_alert=True)
            return
        thread_id = message.message_thread_id if message.is_topic_message else None
        result = await self._broadcasts.start(
            callback.from_user.id,
            PreviewRef(message.chat.id, message.message_id),
            message.html_text,
            TelegramBroadcastReporter(bot, texts, message.chat.id, thread_id),
        )
        refusal = START_REFUSALS.get(result.outcome)
        if refusal is not None:
            await callback.answer(texts.get(refusal), show_alert=True)
            if result.outcome is StartOutcome.ALREADY_HANDLED:
                await _remove_buttons(message)
            return
        await callback.answer()
        await _remove_buttons(message)


def create_admin_broadcast_router(access: AdminAccess, broadcasts: BroadcastService) -> Router:
    router = Router(name="admin_broadcast")
    admin = AdminFilter(access)
    handlers = AdminBroadcastHandlers(broadcasts)
    router.message.register(handlers.on_command, admin, Command(BROADCAST_COMMAND))
    router.callback_query.register(handlers.on_press, admin, AdminBroadcastCallback.filter())
    # Сюда доходят только нажатия, которые не прошли `AdminFilter`
    router.callback_query.register(on_foreign_press, AdminBroadcastCallback.filter())
    return router
