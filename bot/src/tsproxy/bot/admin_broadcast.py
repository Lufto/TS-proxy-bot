"""`/broadcast` в группе администраторов (задачи 19 и 32): рассылка с предпросмотром.

Текстом (задача 19):
1. `/broadcast <текст>` — бот присылает шапку (сколько получат) и предпросмотр: текст рассылки
   ровно так, как его увидят клиенты, с кнопками «✅ Отправить» и «✖️ Отмена».
   Форматирование администратора (жирный, ссылки) переводится в HTML из разметки Telegram.
2. «✅ Отправить» — текст берётся из самого сообщения предпросмотра (`html_text`) и уходит
   с тем же режимом разметки (HTML), что и предпросмотр: клиенты видят то же, что видел
   администратор. Ничего не дописываем и не экранируем молча.

Копией сообщения (задача 32): фото, видео, файл, голосовое… с подписью и разметкой.
1. Администратор присылает сообщение как есть и отвечает на него `/broadcast` (без текста).
   Бот копирует его (`copyMessage`, без «переслано от») в ту же тему — это предпросмотр,
   и ответом на копию присылает «получат N…» с кнопками «✅ Отправить» и «✖️ Отмена».
2. «✅ Отправить» — клиентам уходит копия той самой копии-предпросмотра: её прислал бот,
   администраторы её не изменят. Какую копию слать — Telegram сообщает сам: это сообщение,
   на которое отвечает «получат N…» (`reply_to_message`); из кнопки ничего не берём.
3. Альбомы, опросы, стикеры, геопозиции и сообщения с кнопками не рассылаем — честный отказ.

Общее: ход и итог рассылки — в ту же тему; под «Рассылка началась» — «⏹ Остановить».

Повторное «✅ Отправить», второй администратор, отправка после «Отмены» — `BroadcastService`
находит запись в журнале и ничего не шлёт. Права — `AdminFilter` на каждом обработчике.
"""

import logging
import math
from collections.abc import Awaitable
from enum import StrEnum

from aiogram import Bot, Router
from aiogram.enums import ContentType, MessageEntityType, ParseMode
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyParameters,
)
from aiogram.utils.text_decorations import add_surrogates, html_decoration, remove_surrogates

from tsproxy.bot.admin import AdminAccess, AdminFilter
from tsproxy.bot.admin_clients import on_foreign_press
from tsproxy.bot.admin_stats import format_duration
from tsproxy.bot.context import BotContext
from tsproxy.bot.delivery import MESSAGE_NOT_MODIFIED, ClientDelivery
from tsproxy.content.loader import Texts
from tsproxy.services.broadcast import (
    BROADCAST_SEND_INTERVAL,
    BroadcastContent,
    BroadcastCopy,
    BroadcastEnd,
    BroadcastProgress,
    BroadcastReport,
    BroadcastService,
    BroadcastText,
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
# Что можно разослать копией: у этих сообщений есть смысл для клиента, а копия — ровно
# то, что видел администратор. Опросы (голоса и «викторины» у каждого свои), стикеры,
# геопозиции, контакты, кубики, служебные сообщения и прочее — не рассылаем.
COPYABLE_CONTENT: frozenset[ContentType] = frozenset(
    {
        ContentType.TEXT,
        ContentType.PHOTO,
        ContentType.VIDEO,
        ContentType.ANIMATION,
        ContentType.DOCUMENT,
        ContentType.AUDIO,
        ContentType.VOICE,
        ContentType.VIDEO_NOTE,
    }
)


class BroadcastAction(StrEnum):
    SEND = "send"  # текстовая рассылка: текст — в сообщении с кнопками
    SEND_COPY = "send_copy"  # копией: что слать — сообщение, на которое отвечают кнопки
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


def replied_message(message: Message) -> Message | None:
    """На какое сообщение администратор ответил командой. `None` — не ответ.

    В теме Telegram помечает каждое сообщение ответом на служебное «тема создана» —
    это не ответ администратора.
    """
    replied = message.reply_to_message
    if replied is None or replied.forum_topic_created is not None:
        return None
    if message.is_topic_message and replied.message_id == message.message_thread_id:
        return None
    return replied


def copy_refusal(source: Message) -> str | None:
    """Почему это сообщение нельзя разослать копией — ключ текста отказа. `None` — можно."""
    if source.media_group_id is not None:
        # Альбом Telegram копирует по одному файлу — клиенты получили бы часть альбома
        return "admin.broadcast.album"
    if source.content_type not in COPYABLE_CONTENT:
        return "admin.broadcast.unsupported"
    if source.reply_markup is not None:
        # Копия сохранила бы кнопки — чужие или админские, клиентам они ни к чему
        return "admin.broadcast.has_buttons"
    return None


# --- Отправка и отчёты ---


class TelegramBroadcastSender:
    """Отправка одного сообщения рассылки (реализует `services.broadcast.BroadcastSender`).

    Заблокировавших помечает `ClientDelivery` — им больше не шлём ни рассылок, ни уведомлений.
    """

    def __init__(self, bot: Bot, delivery: ClientDelivery) -> None:
        self._bot = bot
        self._delivery = delivery

    async def send(self, telegram_id: int, content: BroadcastContent) -> DeliveryOutcome:
        try:
            sent = await self._delivery.call(telegram_id, self._request(telegram_id, content))
        except TelegramRetryAfter as error:
            raise BroadcastThrottled(error.retry_after) from error
        except (TelegramBadRequest, TelegramNetworkError, TelegramServerError) as error:
            # Один клиент не получил — рассылка идёт дальше; неудачи считает сервис
            logger.warning("Рассылка: клиенту %s не отправлено: %s", telegram_id, error)
            return DeliveryOutcome.FAILED
        return DeliveryOutcome.BLOCKED if sent is None else DeliveryOutcome.DELIVERED

    def _request(self, telegram_id: int, content: BroadcastContent) -> Awaitable[object]:
        match content:
            case BroadcastText(html=html):
                return self._bot.send_message(telegram_id, html, parse_mode=ParseMode.HTML)
            case BroadcastCopy(chat_id=chat_id, message_id=message_id):
                # Подпись и разметку не передаём — Telegram оставит их как в предпросмотре
                return self._bot.copy_message(telegram_id, chat_id, message_id)


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


def confirm_markup(
    texts: Texts, send: BroadcastAction = BroadcastAction.SEND
) -> InlineKeyboardMarkup:
    def button(key: str, action: BroadcastAction) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=texts.get(key), callback_data=AdminBroadcastCallback(action=action).pack()
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button("admin.broadcast.send_button", send),
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

    async def on_command(self, message: Message, bot: Bot, ctx: BotContext) -> None:
        texts = ctx.admin_texts
        body = command_body_html(message)
        source = replied_message(message)
        if source is not None:
            if body is not None:
                await message.answer(texts.get("admin.broadcast.reply_or_text"))
                return
            await self._preview_copy(message, source, bot, texts)
            return
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

    async def _preview_copy(
        self, command: Message, source: Message, bot: Bot, texts: Texts
    ) -> None:
        """Копия сообщения в ту же тему и под ней — «получат N…» с кнопками."""
        refusal = copy_refusal(source)
        if refusal is not None:
            await command.answer(texts.get(refusal))
            return
        thread_id = command.message_thread_id if command.is_topic_message else None
        try:
            preview = await bot.copy_message(
                command.chat.id, command.chat.id, source.message_id, message_thread_id=thread_id
            )
        except TelegramBadRequest as error:
            # Например, сообщение уже удалили или его запрещено копировать
            logger.info("Предпросмотр рассылки (копия) не принят Telegram: %s", error.message)
            await command.answer(texts.get("admin.broadcast.preview_failed", error=error.message))
            return
        audience = await self._broadcasts.audience()
        await command.answer(
            texts.get(
                "admin.broadcast.copy_header",
                recipients=audience.recipients,
                blocked=audience.blocked,
            ),
            reply_parameters=ReplyParameters(message_id=preview.message_id),
            reply_markup=confirm_markup(texts, BroadcastAction.SEND_COPY),
        )

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
                await self._send_text(callback, message, bot, texts)
            case BroadcastAction.SEND_COPY:
                await self._send_copy(callback, message, bot, texts)
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

    async def _send_text(
        self, callback: CallbackQuery, message: Message, bot: Bot, texts: Texts
    ) -> None:
        if not message.text:
            await callback.answer(texts.get("admin.broadcast.preview_expired"), show_alert=True)
            return
        await self._start(callback, message, bot, texts, BroadcastText(message.html_text))

    async def _send_copy(
        self, callback: CallbackQuery, message: Message, bot: Bot, texts: Texts
    ) -> None:
        """Слать копию-предпросмотр, на которую отвечает сообщение с кнопками.

        Копию удалили — Telegram не присылает, на что был ответ: отправить нечего.
        Ответ не на сообщение бота — не наш предпросмотр, не шлём.
        """
        preview = message.reply_to_message
        if preview is None or preview.from_user is None or preview.from_user.id != bot.id:
            await callback.answer(texts.get("admin.broadcast.preview_missing"), show_alert=True)
            await _remove_buttons(message)
            return
        content = BroadcastCopy(chat_id=message.chat.id, message_id=preview.message_id)
        await self._start(callback, message, bot, texts, content)

    async def _start(
        self,
        callback: CallbackQuery,
        message: Message,
        bot: Bot,
        texts: Texts,
        content: BroadcastContent,
    ) -> None:
        """Запустить рассылку; «один раз на предпросмотр» — по сообщению с кнопками."""
        thread_id = message.message_thread_id if message.is_topic_message else None
        result = await self._broadcasts.start(
            callback.from_user.id,
            PreviewRef(message.chat.id, message.message_id),
            content,
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
