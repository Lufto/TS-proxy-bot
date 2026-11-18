"""Поддержка через бота (задача 16): обращение = тема в группе администраторов.

- Любое обычное сообщение клиента (не команда) — сообщение в поддержку. Первое открывает
  обращение: бот создаёт тему в группе и отвечает клиенту «Обращение принято» — без номера,
  с кнопкой «В меню» (задача bot-telegram-ui/14).
  Сообщение копируется в тему как есть (текст, фото, файлы, голосовые, видео); стикеры,
  контакты, геолокацию и т. п. не передаём. Больше 10 сообщений в минуту — просим подождать.
- Сообщение администратора в теме обращения уходит клиенту от «Поддержки», под ним —
  кнопка «В меню» (как и под быстрым ответом). Ответ и «Обращение принято» — сообщения
  с сохраняемым текстом: при нажатии «В меню» у них гаснут только кнопки (`bot.delivery`).
  Начинается с `//` — внутренняя заметка, клиенту не уходит. Команды (`/…`) клиенту не уходят.
- Ответ на конкретное сообщение (reply) с одной стороны приходит ответом и с другой.
- Переписка — в базе (`services.support`). Тексты сообщений в лог не пишем.
- Задача 17: экран «Поддержка» сначала показывает частые вопросы; в начале темы — карточка
  клиента с кнопками (`bot.support_card`, `bot.support_actions`); клиент пишет в течение
  суток после закрытия — обращение переоткрывается, в теме — новая карточка. Напоминание
  о забытом обращении — `bot.support_reminders`.

Клиент — всегда `from_user.id` отправителя. Действия в группе — только от `ADMIN_IDS`
и только в группе администраторов (`AdminFilter`).
"""

import html
import logging

from aiogram import Bot, F, Router
from aiogram.enums import ChatType, ContentType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, MessageId, ReplyParameters, User

from tsproxy.bot.admin import AdminAccess, AdminFilter
from tsproxy.bot.callbacks import FaqCallback, MenuCallback, ScreenId
from tsproxy.bot.context import BotContext, profile_of
from tsproxy.bot.delivery import kept_markup
from tsproxy.bot.support_actions import (
    CardClientActions,
    register_ticket_actions,
)
from tsproxy.bot.support_card import SupportTopicTexts
from tsproxy.bot.throttling import RateDecision, is_command
from tsproxy.db.enums import MessageAuthor
from tsproxy.domain.support import fit_topic_name, is_internal_note
from tsproxy.services.support import MessageContent, TicketRef

logger = logging.getLogger(__name__)

SUPPORT_COMMAND = "support"
# Задача 33: обязательна по правилам Telegram для ботов с оплатой — тот же экран поддержки
PAYSUPPORT_COMMAND = "paysupport"
# Ключ склейки тревоги «не создаётся тема обращения»
SUPPORT_TOPIC_ALERT = "support_topic_failed"
# Ответ Telegram, если тему обращения удалили вручную
TOPIC_NOT_FOUND = "message thread not found"

# Что передаём между клиентом и поддержкой. Стикеры — нет (решение владельца); контакты —
# нет (телефоны не храним); геолокация, опросы, игры и прочее поддержке не нужны.
FILE_KINDS = frozenset(
    {
        ContentType.PHOTO,
        ContentType.DOCUMENT,
        ContentType.VOICE,
        ContentType.VIDEO,
        ContentType.AUDIO,
        ContentType.ANIMATION,
        ContentType.VIDEO_NOTE,
    }
)
# У «кружочка» не бывает подписи — его ответ уходит клиенту без «Поддержка:»
CAPTIONLESS_KINDS = frozenset({ContentType.VIDEO_NOTE})


def content_of(message: Message) -> MessageContent | None:
    """Что сохранить в переписку. `None` — такое сообщение не передаём."""
    kind = message.content_type
    if kind == ContentType.TEXT:
        return MessageContent(text=message.text)
    if kind not in FILE_KINDS:
        return None
    return MessageContent(text=message.caption, file_id=_file_id(message), file_kind=kind.value)


def _file_id(message: Message) -> str | None:
    if message.photo:
        return message.photo[-1].file_id  # самый большой размер
    media = (
        message.animation  # у GIF заполнен и `document` — проверяем раньше
        or message.document
        or message.voice
        or message.video
        or message.audio
        or message.video_note
    )
    return None if media is None else media.file_id


def create_support_router(access: AdminAccess, client_actions: CardClientActions) -> Router:
    """Роутер поддержки. Подключается после остальных: берёт все обычные сообщения клиента.

    `client_actions` — «➕ Продлить» и «📱 Устройства» из карточки (действия админки, задача 18).
    """
    router = Router(name="support")
    private = F.chat.type == ChatType.PRIVATE
    private_button = F.message.chat.type == ChatType.PRIVATE
    router.message.register(
        on_support_command, private, Command(SUPPORT_COMMAND, PAYSUPPORT_COMMAND)
    )
    router.callback_query.register(
        on_support_screen, private_button, MenuCallback.filter(F.screen == ScreenId.SUPPORT)
    )
    router.callback_query.register(on_faq, private_button, FaqCallback.filter())
    register_ticket_actions(router, access, client_actions)
    router.message.register(on_client_message, private, lambda m: not is_command(m))
    router.message.register(
        on_topic_message,
        AdminFilter(access),
        F.is_topic_message,
        F.message_thread_id,
        lambda m: not is_command(m),
    )
    return router


# --- Экран «Поддержка» ---


async def on_support_command(message: Message, ctx: BotContext) -> None:
    if message.from_user is None:
        return
    await ctx.clients.resolve(profile_of(message.from_user))
    await ctx.delivery.send(message.from_user.id, ctx.screens.support())


async def on_support_screen(callback: CallbackQuery, ctx: BotContext) -> None:
    await callback.answer()
    await ctx.delivery.show(callback, ctx.screens.support())


async def on_faq(callback: CallbackQuery, callback_data: FaqCallback, ctx: BotContext) -> None:
    """Ответ на частый вопрос — новым экраном (живое меню)."""
    await callback.answer()
    await ctx.delivery.show(callback, ctx.screens.faq_answer(callback_data.question))


# --- Клиент → тема ---


async def on_client_message(message: Message, bot: Bot, ctx: BotContext) -> None:
    """Обычное сообщение клиента — в тему его обращения."""
    sender = message.from_user
    if sender is None:
        return  # сообщение от имени канала — не клиент
    decision = ctx.support_guard.check_flood(sender.id)
    if decision is RateDecision.DENY_FIRST:
        await _tell_client(ctx, message, ctx.texts.get("support.flood"))
    if decision is not RateDecision.ALLOW:
        return
    content = content_of(message)
    if content is None:
        await _tell_client(ctx, message, ctx.texts.get("support.unsupported"))
        return
    # по одному сообщению клиента за раз: альбом не откроет несколько обращений
    async with ctx.support_guard.lock(sender.id):
        await _forward_to_topic(message, sender, content, bot, ctx)


async def _forward_to_topic(
    message: Message, sender: User, content: MessageContent, bot: Bot, ctx: BotContext
) -> None:
    client = await ctx.clients.resolve(profile_of(sender))
    ticket = await ctx.support.current_ticket(client.id)
    first_in_topic = ticket.topic_id is None
    topic_id = ticket.topic_id or await _open_topic(bot, ctx, ticket, sender)
    if ticket.reopened and ticket.topic_id is not None:
        # переоткрыто в той же теме — свежая карточка с кнопками внизу темы
        await _send_card(bot, ctx, ticket, ticket.topic_id)
    copied = None
    if topic_id is not None:
        copied = await _copy_to_topic(message, sender, ticket, topic_id, bot, ctx)
    if copied is None:
        await _tell_client(ctx, message, ctx.texts.get("support.not_delivered"))
        return
    await ctx.support.record_message(
        ticket.id,
        MessageAuthor.CLIENT,
        content,
        client_message_id=message.message_id,
        group_message_id=copied.message_id,
    )
    logger.info("Обращение #%s: сообщение клиента %s", ticket.id, sender.id)
    if first_in_topic:
        await ctx.delivery.send(sender.id, ctx.screens.ticket_accepted())


async def _open_topic(bot: Bot, ctx: BotContext, ticket: TicketRef, sender: User) -> int | None:
    """Создать тему обращения и карточку в ней. `None` — Telegram не дал создать тему."""
    # название темы — обычный текст: подстановки в текстах экранируются для HTML, снимаем это
    name = html.unescape(
        ctx.admin_texts.get("support.topic_name", ticket_id=ticket.id, name=sender.full_name)
    )
    try:
        topic = await bot.create_forum_topic(ctx.access.admin_chat_id, fit_topic_name(name))
    except (TelegramBadRequest, TelegramForbiddenError) as error:
        # группа не форум, у бота нет права «Управление темами», бота убрали из группы
        logger.error("Обращение #%s: тема не создана: %s", ticket.id, error.message)
        await ctx.alerter.alert(
            SUPPORT_TOPIC_ALERT, source=_open_topic, error=error, ticket_id=ticket.id
        )
        return None
    topic_id = topic.message_thread_id
    await ctx.support.attach_topic(ticket.id, topic_id)
    logger.info("Обращение #%s: тема %s", ticket.id, topic_id)
    await _send_card(bot, ctx, ticket, topic_id)
    return topic_id


async def _send_card(bot: Bot, ctx: BotContext, ticket: TicketRef, topic_id: int) -> None:
    """Карточка клиента с кнопками — в начале темы или при переоткрытии обращения."""
    card = await ctx.client_cards.card(ticket.client_telegram_id)
    if card is None:
        # клиента только что записали в базу в этом же обработчике
        raise LookupError(f"Обращение #{ticket.id}: клиента нет в базе")
    screen = SupportTopicTexts(ctx.admin_texts).card(ticket.id, card, reopened=ticket.reopened)
    await bot.send_message(
        ctx.access.admin_chat_id,
        screen.text,
        message_thread_id=topic_id,
        reply_markup=screen.markup,
    )


async def _copy_to_topic(
    message: Message, sender: User, ticket: TicketRef, topic_id: int, bot: Bot, ctx: BotContext
) -> MessageId | None:
    """Копия сообщения клиента в тему. Тему удалили вручную — создаём новую и пробуем ещё раз."""
    reply_to = None
    if message.reply_to_message is not None:
        reply_to = await ctx.support.group_message_for(
            ticket.id, message.reply_to_message.message_id
        )
    try:
        return await _copy(message, topic_id, reply_to, bot, ctx)
    except TelegramBadRequest as error:
        if TOPIC_NOT_FOUND not in error.message:
            raise
    logger.warning("Обращение #%s: тема %s удалена, создаём новую", ticket.id, topic_id)
    new_topic_id = await _open_topic(bot, ctx, ticket, sender)
    if new_topic_id is None:
        return None
    return await _copy(message, new_topic_id, None, bot, ctx)


async def _copy(
    message: Message, topic_id: int, reply_to: int | None, bot: Bot, ctx: BotContext
) -> MessageId:
    return await bot.copy_message(
        ctx.access.admin_chat_id,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        message_thread_id=topic_id,
        reply_parameters=_reply(reply_to),
    )


async def _tell_client(ctx: BotContext, message: Message, text: str) -> None:
    if message.from_user is None:
        return
    await ctx.delivery.call(message.from_user.id, message.answer(text))


# --- Тема → клиент ---


async def on_topic_message(message: Message, bot: Bot, ctx: BotContext) -> None:
    """Сообщение администратора в теме обращения: ответ клиенту или заметка `//`."""
    if message.message_thread_id is None or message.from_user is None:
        return
    ticket = await ctx.support.ticket_by_topic(message.message_thread_id)
    if ticket is None:
        return  # тема не обращения: «🚨 Тревоги», «💰 Оплаты» и т. п.
    content = content_of(message)
    if content is None:
        await message.reply(ctx.admin_texts.get("support.reply_unsupported"))
        return
    if is_internal_note(content.text):
        await ctx.support.record_message(
            ticket.id,
            MessageAuthor.NOTE,
            content,
            client_message_id=None,
            group_message_id=message.message_id,
        )
        return
    await _reply_to_client(message, ticket, content, bot, ctx)


async def _reply_to_client(
    message: Message, ticket: TicketRef, content: MessageContent, bot: Bot, ctx: BotContext
) -> None:
    reply_to = None
    if message.reply_to_message is not None:
        reply_to = await ctx.support.client_message_for(
            ticket.id, message.reply_to_message.message_id
        )
    try:
        delivered = await ctx.delivery.call(
            ticket.client_telegram_id, _send_reply(message, ticket, reply_to, bot, ctx)
        )
    except TelegramBadRequest as error:
        # например, текст с подписью «Поддержка:» длиннее предела Telegram — администратор
        # видит причину в теме и может отправить иначе; в переписку не пишем — клиент не получил
        logger.warning("Обращение #%s: ответ не отправлен: %s", ticket.id, error.message)
        await message.reply(ctx.admin_texts.get("support.reply_failed", error=error.message))
        return
    if delivered is None:
        await message.reply(ctx.admin_texts.get("support.client_blocked"))
    await ctx.support.record_message(
        ticket.id,
        MessageAuthor.SUPPORT,
        content,
        client_message_id=None if delivered is None else delivered.message_id,
        group_message_id=message.message_id,
    )
    logger.info("Обращение #%s: ответ поддержки отправлен клиенту", ticket.id)


async def _send_reply(
    message: Message, ticket: TicketRef, reply_to: int | None, bot: Bot, ctx: BotContext
) -> Message | MessageId:
    """Отправить ответ клиенту с подписью «Поддержка:» (разметка администратора сохраняется)."""
    client_id = ticket.client_telegram_id
    prefix = ctx.texts.get("support.reply_prefix")
    to_menu = kept_markup(ctx.screens.to_menu())  # текст ответа при нажатии не удаляется
    if message.content_type == ContentType.TEXT:
        return await bot.send_message(
            client_id,
            prefix + message.html_text,
            reply_parameters=_reply(reply_to),
            reply_markup=to_menu,
        )
    caption = None if message.content_type in CAPTIONLESS_KINDS else prefix + message.html_text
    return await bot.copy_message(
        client_id,
        from_chat_id=message.chat.id,
        message_id=message.message_id,
        caption=caption,
        reply_parameters=_reply(reply_to),
        reply_markup=to_menu,
    )


def _reply(message_id: int | None) -> ReplyParameters | None:
    """Ответить на сообщение, если оно ещё есть; удалено — отправить просто так."""
    if message_id is None:
        return None
    return ReplyParameters(message_id=message_id, allow_sending_without_reply=True)
