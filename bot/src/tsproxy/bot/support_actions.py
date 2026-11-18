"""Кнопки в теме обращения (задача 17): «🙋 Беру», «✅ Закрыть», быстрые ответы,
«➕ Продлить на N дней» и «📱 Устройства».

Нажать может только администратор и только в группе администраторов (`AdminFilter` на каждом
обработчике). Номер обращения в кнопке — не доступ: обращение каждый раз читается из базы.
«Беру» и «Закрыть» пишутся в журнал событий (`SupportService`), обычные ответы — нет.

«➕ Продлить», «📱 Устройства» и «↩️ Вернуть» — действия админки (задачи 18, 21).
Здесь только кнопки и точка подключения `CardClientActions`; реализация —
`bot.admin_clients.AdminCardClientActions`.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters.callback_data import CallbackQueryFilter
from aiogram.types import CallbackQuery, Message

from tsproxy.bot.admin import AdminAccess, AdminFilter
from tsproxy.bot.callbacks import QuickReplyCallback, TicketAction, TicketCallback
from tsproxy.bot.context import BotContext
from tsproxy.bot.delivery import kept_markup
from tsproxy.bot.support_card import SupportTopicTexts
from tsproxy.db.enums import MessageAuthor
from tsproxy.services.support import MessageContent, TicketChange, TicketRef

logger = logging.getLogger(__name__)

# Telegram: всплывающий ответ на кнопку — до 200 символов
CALLBACK_ANSWER_MAX = 200


class CardClientActions(Protocol):
    """«➕ Продлить на N дней», «📱 Устройства» (задача 18) и «↩️ Вернуть» (задача 21)
    в карточке обращения — логика админки.

    Права администратора уже проверены; клиент — `ticket.client_telegram_id`.
    """

    async def extend(self, callback: CallbackQuery, ticket: TicketRef, ctx: BotContext) -> None: ...

    async def devices(
        self, callback: CallbackQuery, ticket: TicketRef, ctx: BotContext
    ) -> None: ...

    async def refund(self, callback: CallbackQuery, ticket: TicketRef, ctx: BotContext) -> None: ...


def register_ticket_actions(
    router: Router, access: AdminAccess, client_actions: CardClientActions
) -> None:
    """Подключить обработчики кнопок темы к роутеру поддержки."""
    admin = AdminFilter(access)

    def on(action: TicketAction) -> CallbackQueryFilter:
        return TicketCallback.filter(F.action == action)

    router.callback_query.register(on_take, admin, on(TicketAction.TAKE))
    router.callback_query.register(on_close, admin, on(TicketAction.CLOSE))
    router.callback_query.register(on_quick_replies, admin, on(TicketAction.QUICK_REPLIES))
    router.callback_query.register(on_hide, admin, on(TicketAction.HIDE))
    router.callback_query.register(on_quick_reply, admin, QuickReplyCallback.filter())
    router.callback_query.register(
        _client_action(client_actions.extend), admin, on(TicketAction.EXTEND)
    )
    router.callback_query.register(
        _client_action(client_actions.devices), admin, on(TicketAction.DEVICES)
    )
    router.callback_query.register(
        _client_action(client_actions.refund), admin, on(TicketAction.REFUND)
    )


type ClientAction = Callable[[CallbackQuery, TicketRef, BotContext], Awaitable[None]]


def _client_action(
    action: ClientAction,
) -> Callable[[CallbackQuery, TicketCallback, BotContext], Awaitable[None]]:
    async def handler(
        callback: CallbackQuery, callback_data: TicketCallback, ctx: BotContext
    ) -> None:
        ticket = await ctx.support.ticket(callback_data.ticket_id)
        if ticket is None:
            await _stale(callback, ctx)
            return
        await action(callback, ticket, ctx)

    return handler


# --- «🙋 Беру» и «✅ Закрыть» ---


async def on_take(
    callback: CallbackQuery, callback_data: TicketCallback, bot: Bot, ctx: BotContext
) -> None:
    admin = callback.from_user
    change = await ctx.support.take(callback_data.ticket_id, admin.id)
    if change is TicketChange.DONE:
        logger.info("Обращение #%s: взял администратор %s", callback_data.ticket_id, admin.id)
        await callback.answer()
        texts = SupportTopicTexts(ctx.admin_texts)
        await _post(bot, ctx, callback_data.ticket_id, texts.taken(admin.full_name))
        return
    answers = {
        TicketChange.UNCHANGED: "support.already_yours",
        TicketChange.CLOSED: "support.take_closed",
        TicketChange.NOT_FOUND: "support.ticket_not_found",
    }
    await callback.answer(ctx.admin_texts.get(answers[change]), show_alert=True)


async def on_close(
    callback: CallbackQuery, callback_data: TicketCallback, bot: Bot, ctx: BotContext
) -> None:
    admin = callback.from_user
    change = await ctx.support.close(callback_data.ticket_id, admin.id)
    if change is TicketChange.DONE:
        logger.info("Обращение #%s: закрыл администратор %s", callback_data.ticket_id, admin.id)
        await callback.answer()
        texts = SupportTopicTexts(ctx.admin_texts)
        await _post(bot, ctx, callback_data.ticket_id, texts.closed(admin.full_name))
        return
    answers = {
        TicketChange.UNCHANGED: "support.already_closed",
        TicketChange.NOT_FOUND: "support.ticket_not_found",
    }
    await callback.answer(ctx.admin_texts.get(answers[change]), show_alert=True)


async def _post(bot: Bot, ctx: BotContext, ticket_id: int, text: str) -> None:
    """Сообщение в теме обращения: кто взял, кто закрыл."""
    ticket = await ctx.support.ticket(ticket_id)
    if ticket is None or ticket.topic_id is None:
        # кнопка была в теме — значит, тема есть; сюда попасть не должны
        logger.warning("Обращение #%s: нет темы, сообщение не отправлено", ticket_id)
        return
    await bot.send_message(ctx.access.admin_chat_id, text, message_thread_id=ticket.topic_id)


async def _stale(callback: CallbackQuery, ctx: BotContext) -> None:
    await callback.answer(ctx.admin_texts.get("support.ticket_not_found"), show_alert=True)


# --- Быстрые ответы ---


async def on_quick_replies(
    callback: CallbackQuery, callback_data: TicketCallback, bot: Bot, ctx: BotContext
) -> None:
    """Показать меню заготовок внизу темы — карточка с кнопками могла уехать далеко вверх."""
    ticket = await ctx.support.ticket(callback_data.ticket_id)
    if ticket is None or ticket.topic_id is None:
        await _stale(callback, ctx)
        return
    await callback.answer()
    menu = SupportTopicTexts(ctx.admin_texts).quick_replies_menu(ticket.id)
    await bot.send_message(
        ctx.access.admin_chat_id,
        menu.text,
        message_thread_id=ticket.topic_id,
        reply_markup=menu.markup,
    )


async def on_hide(callback: CallbackQuery) -> None:
    """«✖️ Скрыть» меню заготовок."""
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.delete()


async def on_quick_reply(
    callback: CallbackQuery, callback_data: QuickReplyCallback, bot: Bot, ctx: BotContext
) -> None:
    """Заготовка уходит клиенту от «Поддержки»; меню превращается в запись «что отправлено»."""
    texts = SupportTopicTexts(ctx.admin_texts)
    if callback_data.reply not in texts.quick_reply_ids():
        await callback.answer(ctx.admin_texts.get("support.quick_reply_not_found"), show_alert=True)
        return
    ticket = await ctx.support.ticket(callback_data.ticket_id)
    if ticket is None:
        await _stale(callback, ctx)
        return
    text = texts.quick_reply_text(callback_data.reply)
    client_id = ticket.client_telegram_id
    try:
        delivered = await ctx.delivery.call(
            client_id,
            bot.send_message(
                client_id,
                ctx.texts.get("support.reply_prefix") + text,
                reply_markup=kept_markup(ctx.screens.to_menu()),
            ),
        )
    except TelegramBadRequest as error:
        logger.warning("Обращение #%s: заготовка не отправлена: %s", ticket.id, error.message)
        answer = ctx.admin_texts.get("support.reply_failed", error=error.message)
        await callback.answer(answer[:CALLBACK_ANSWER_MAX], show_alert=True)
        return
    await callback.answer()
    sent = texts.quick_reply_sent(callback.from_user.full_name, callback_data.reply)
    shown = await _show_sent(callback, ticket, sent, bot, ctx)
    if delivered is None and ticket.topic_id is not None:
        await bot.send_message(
            ctx.access.admin_chat_id,
            ctx.admin_texts.get("support.client_blocked"),
            message_thread_id=ticket.topic_id,
        )
    await ctx.support.record_message(
        ticket.id,
        MessageAuthor.SUPPORT,
        MessageContent(text=text),
        client_message_id=None if delivered is None else delivered.message_id,
        group_message_id=None if shown is None else shown.message_id,
    )
    logger.info("Обращение #%s: быстрый ответ %s отправлен", ticket.id, callback_data.reply)


async def _show_sent(
    callback: CallbackQuery, ticket: TicketRef, text: str, bot: Bot, ctx: BotContext
) -> Message | None:
    """Меню заготовок → «отправлено клиенту: …». Не изменить — новое сообщение в теме."""
    menu = callback.message
    if isinstance(menu, Message):
        try:
            edited = await menu.edit_text(text)
        except TelegramBadRequest as error:
            logger.info("Меню заготовок не изменить (%s), пишем новое", error.message)
        else:
            return edited if isinstance(edited, Message) else menu
    if ticket.topic_id is None:
        return None
    return await bot.send_message(ctx.access.admin_chat_id, text, message_thread_id=ticket.topic_id)
