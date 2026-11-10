"""Админка в группе администраторов (задача 18): поиск клиента, карточка и действия.

- `/client 123456789` или `/client @username` — карточка: подписка, оплаты, пробный,
  устройства, кнопки действий. Несколько клиентов с одним @username — список с ID.
- `/give 123456789 30` или «➕ N дн.» под карточкой — предпросмотр «сейчас / станет»
  и «✅ Да» (аудит R22). Предел — `MAX_MANUAL_GRANT_DAYS` за раз.
- «📱 Устройства» → «🗑 N»; «🎁 Сбросить пробный» и «🔄 Перевыпустить ссылку» — с подтверждением.

Права: каждый обработчик стоит за `AdminFilter` — нажавший или написавший из `ADMIN_IDS`
и сообщение в группе администраторов. Данные кнопки прав не дают. Кнопку админки нажал
кто-то другой — всплывающий отказ и запись в лог, ничего не делается.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, or_f
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tsproxy.bot.admin import AdminAccess, AdminFilter
from tsproxy.bot.admin_callbacks import (
    AdminAction,
    AdminClientCallback,
    AdminDeviceCallback,
    AdminExtendCallback,
    stamp_expire,
)
from tsproxy.bot.admin_refund_screens import refund_list_screen
from tsproxy.bot.admin_screens import AdminScreen, AdminScreens
from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.connect import PANEL_DOWN_ERRORS
from tsproxy.bot.context import BotContext
from tsproxy.bot.menu import HELP_COMMAND
from tsproxy.bot.screens import Screen
from tsproxy.domain.admin import GrantDaysOutOfRange, parse_client_query
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.profile import plural_form
from tsproxy.services.admin import ExtensionOutcome, RevokeOutcome, TrialResetOutcome
from tsproxy.services.support import TicketRef

logger = logging.getLogger(__name__)

CLIENT_COMMAND = "client"
GIVE_COMMAND = "give"
# `/give <Telegram ID> <дней>`
GIVE_ARGUMENTS = 2

RESET_TEXTS: dict[TrialResetOutcome, str] = {
    TrialResetOutcome.RESET: "admin.reset.done",
    TrialResetOutcome.NOT_USED: "admin.reset.not_used",
    TrialResetOutcome.HAS_PAID: "admin.reset.has_paid",
    TrialResetOutcome.CLIENT_NOT_FOUND: "admin.client_not_found",
}
REVOKE_TEXTS: dict[RevokeOutcome, str] = {
    RevokeOutcome.REVOKED: "admin.revoke.done",
    RevokeOutcome.NO_ACCOUNT: "admin.revoke.no_account",
    RevokeOutcome.CLIENT_NOT_FOUND: "admin.client_not_found",
}


def create_admin_router(access: AdminAccess) -> Router:
    """Роутер админки. Команды не от администраторов или не в группе — молча не берёт."""
    router = Router(name="admin")
    admin = AdminFilter(access)
    router.message.register(on_client_command, admin, Command(CLIENT_COMMAND))
    router.message.register(on_give_command, admin, Command(GIVE_COMMAND))
    router.message.register(on_help_command, admin, Command(HELP_COMMAND))
    router.callback_query.register(on_client_action, admin, AdminClientCallback.filter())
    router.callback_query.register(on_extend, admin, AdminExtendCallback.filter())
    router.callback_query.register(on_delete_device, admin, AdminDeviceCallback.filter())
    # Сюда доходят только нажатия, которые не прошли `AdminFilter`
    router.callback_query.register(
        on_foreign_press,
        or_f(
            AdminClientCallback.filter(),
            AdminExtendCallback.filter(),
            AdminDeviceCallback.filter(),
        ),
    )
    return router


def _screens(ctx: BotContext) -> AdminScreens:
    return AdminScreens(ctx.admin_texts)


async def _send(message: Message, screen: AdminScreen) -> None:
    """Новым сообщением в тот же чат и ту же тему."""
    await message.answer(screen.text, reply_markup=screen.markup)


async def send_from(
    callback: CallbackQuery, bot: Bot, ctx: BotContext, screen: AdminScreen
) -> None:
    """Новым сообщением рядом с сообщением, под которым нажали кнопку."""
    if isinstance(callback.message, Message):
        await _send(callback.message, screen)
        return
    # Сообщение слишком старое (Telegram его не отдаёт) — пишем в группу без темы
    await bot.send_message(ctx.access.admin_chat_id, screen.text, reply_markup=screen.markup)


async def replace_screen(
    callback: CallbackQuery, bot: Bot, ctx: BotContext, screen: AdminScreen
) -> None:
    """Заменить сообщение с кнопками итогом: кнопки подтверждения больше не нажать."""
    if isinstance(callback.message, Message):
        await callback.message.edit_text(screen.text, reply_markup=screen.markup)
        return
    await send_from(callback, bot, ctx, screen)


# --- Команды ---


async def on_help_command(message: Message, ctx: BotContext) -> None:
    """`/help` в группе администраторов — все команды админки."""
    await _send(message, _screens(ctx).usage())


async def on_client_command(message: Message, command: CommandObject, ctx: BotContext) -> None:
    """`/client <ID или @username>` — карточка клиента."""
    screens = _screens(ctx)
    query = None if command.args is None else parse_client_query(command.args)
    if query is None or command.args is None:
        await _send(message, screens.usage())
        return
    clients = await ctx.admin.find_clients(query)
    if not clients:
        await _send(message, screens.not_found(command.args.strip()))
        return
    if len(clients) > 1:
        await _send(message, screens.many_found(clients))
        return
    await _send(message, await _card_screen(ctx, clients[0].telegram_id))


async def _card_screen(ctx: BotContext, telegram_id: int) -> AdminScreen:
    screens = _screens(ctx)
    card = await ctx.admin.card(telegram_id)
    if card is None:
        return screens.client_not_found(telegram_id)
    return screens.card(card)


async def on_give_command(message: Message, command: CommandObject, ctx: BotContext) -> None:
    """`/give <Telegram ID> <дней>` — предпросмотр выдачи. Только по ID (аудит R22)."""
    parts = (command.args or "").split()
    query = parse_client_query(parts[0]) if len(parts) == GIVE_ARGUMENTS else None
    if query is None or query.telegram_id is None or not parts[1].isdigit():
        await _send(message, _screens(ctx).give_usage())
        return
    await _send(message, await _preview_screen(ctx, query.telegram_id, int(parts[1])))


async def _preview_screen(ctx: BotContext, telegram_id: int, days: int) -> AdminScreen:
    screens = _screens(ctx)
    try:
        preview = await ctx.admin.preview_extension(telegram_id, days)
    except GrantDaysOutOfRange:
        return screens.days_out_of_range()
    except PANEL_DOWN_ERRORS as error:
        logger.warning("Предпросмотр выдачи клиенту %s: панель не ответила: %s", telegram_id, error)
        return screens.panel_unavailable(ctx.alerter.explain(error))
    client = None if preview is None else await ctx.clients.find(telegram_id)
    if preview is None or client is None:
        return screens.client_not_found(telegram_id)
    return screens.extend_preview(client, preview)


# --- Кнопки ---


async def on_extend(
    callback: CallbackQuery, callback_data: AdminExtendCallback, bot: Bot, ctx: BotContext
) -> None:
    """«➕ N дн.» → предпросмотр; «✅ Да, выдать» → выдача, если срок не изменился."""
    await callback.answer()
    telegram_id, days = callback_data.telegram_id, callback_data.days
    if not callback_data.confirm:
        await send_from(callback, bot, ctx, await _preview_screen(ctx, telegram_id, days))
        return
    screens = _screens(ctx)
    try:
        result = await ctx.admin.extend(
            callback.from_user.id,
            telegram_id,
            days,
            expected_expire_at=stamp_expire(callback_data.stamp),
        )
    except PANEL_DOWN_ERRORS as error:
        # Сообщение с «✅ Да» оставляем: шаги выдачи абсолютные, повтор безопасен
        logger.warning("Ручная выдача клиенту %s: панель не ответила: %s", telegram_id, error)
        await send_from(callback, bot, ctx, screens.panel_unavailable(ctx.alerter.explain(error)))
        return
    match result.outcome:
        case ExtensionOutcome.EXTENDED if result.expire_at is not None:
            screen = screens.extend_done(telegram_id, days, result.expire_at)
            await _tell_client(ctx, telegram_id, days, result.expire_at)
        case ExtensionOutcome.CHANGED:
            screen = screens.extend_changed(telegram_id, result.expire_at)
        case _:
            screen = screens.client_not_found(telegram_id)
    await replace_screen(callback, bot, ctx, screen)


async def _tell_client(ctx: BotContext, telegram_id: int, days: int, expire_at: datetime) -> None:
    """Клиенту — «вам добавили N дней». Сбой Telegram — только лог: дни уже выданы."""
    connect = InlineKeyboardButton(
        text=ctx.texts.get("buttons.connect"),
        callback_data=MenuCallback(screen=ScreenId.CONNECT).pack(),
    )
    screen = Screen(
        ctx.texts.get(
            "manual_grant.added",
            days=ctx.texts.get(f"units.days_{plural_form(days).value}", n=days),
            expire_at=format_moscow(expire_at),
        ),
        InlineKeyboardMarkup(inline_keyboard=[[connect]]),
    )
    try:
        await ctx.delivery.send(telegram_id, screen)
    except TelegramAPIError:
        logger.exception("Ручная выдача: не удалось сообщить клиенту %s", telegram_id)


async def on_client_action(
    callback: CallbackQuery, callback_data: AdminClientCallback, bot: Bot, ctx: BotContext
) -> None:
    await callback.answer()
    screens = _screens(ctx)
    telegram_id = callback_data.telegram_id
    try:
        match callback_data.action:
            case AdminAction.CARD:
                await send_from(callback, bot, ctx, await _card_screen(ctx, telegram_id))
            case AdminAction.DEVICES:
                await send_from(callback, bot, ctx, await _devices_screen(ctx, telegram_id))
            case AdminAction.RESET_TRIAL_ASK:
                await send_from(callback, bot, ctx, screens.reset_trial_ask(telegram_id))
            case AdminAction.REVOKE_ASK:
                await send_from(callback, bot, ctx, screens.revoke_ask(telegram_id))
            case AdminAction.RESET_TRIAL:
                outcome = await ctx.admin.reset_trial(callback.from_user.id, telegram_id)
                await replace_screen(
                    callback, bot, ctx, screens.result(RESET_TEXTS[outcome], telegram_id)
                )
            case AdminAction.REVOKE:
                revoked = await ctx.admin.revoke_link(callback.from_user.id, telegram_id)
                await replace_screen(
                    callback, bot, ctx, screens.result(REVOKE_TEXTS[revoked], telegram_id)
                )
            case AdminAction.CANCEL:
                await replace_screen(callback, bot, ctx, screens.cancelled())
    except PANEL_DOWN_ERRORS as error:
        logger.warning("Админка, клиент %s: панель не ответила: %s", telegram_id, error)
        await send_from(callback, bot, ctx, screens.panel_unavailable(ctx.alerter.explain(error)))


async def _devices_screen(ctx: BotContext, telegram_id: int) -> AdminScreen:
    devices = await ctx.admin.devices(telegram_id)
    if devices is None:
        return _screens(ctx).result("admin.devices.no_account", telegram_id)
    return _screens(ctx).devices(telegram_id, devices)


async def on_delete_device(
    callback: CallbackQuery, callback_data: AdminDeviceCallback, bot: Bot, ctx: BotContext
) -> None:
    """«🗑 N»: только устройство из списка этого клиента; итог — над обновлённым списком."""
    await callback.answer()
    screens = _screens(ctx)
    telegram_id = callback_data.telegram_id
    try:
        result = await ctx.admin.delete_device(
            callback.from_user.id, telegram_id, callback_data.ref
        )
    except PANEL_DOWN_ERRORS as error:
        logger.warning("Удаление устройства клиента %s: панель не ответила: %s", telegram_id, error)
        await send_from(callback, bot, ctx, screens.panel_unavailable(ctx.alerter.explain(error)))
        return
    if result is None:
        screen = screens.result("admin.devices.no_account", telegram_id)
    else:
        notice = "admin.devices.deleted" if result.deleted else "admin.devices.not_found"
        screen = screens.devices(telegram_id, result.devices, notice_key=notice)
    await replace_screen(callback, bot, ctx, screen)


async def on_foreign_press(callback: CallbackQuery, ctx: BotContext) -> None:
    """Кнопку админки нажал не администратор или не в группе: отказ, ничего не делаем."""
    chat_id = None if callback.message is None else callback.message.chat.id
    logger.warning(
        "Кнопка админки от %s в чате %s отклонена: нет прав", callback.from_user.id, chat_id
    )
    await callback.answer(_screens(ctx).not_admin(), show_alert=True)


# --- Кнопки в карточке обращения (задача 17) ---


class AdminCardClientActions:
    """«➕ Продлить», «📱 Устройства» и «↩️ Вернуть» в карточке обращения — те же экраны,
    что у `/client`.

    Права администратора уже проверил роутер поддержки; клиент — из обращения в базе.
    «Продлить» присылает карточку админки с кнопками «➕ 1 / 7 / 30 дн.»: выдача идёт
    через предпросмотр и подтверждение, как в `/give`.
    """

    async def extend(self, callback: CallbackQuery, ticket: TicketRef, ctx: BotContext) -> None:
        await self._show(callback, ctx, ticket.client_telegram_id, _card_screen)

    async def devices(self, callback: CallbackQuery, ticket: TicketRef, ctx: BotContext) -> None:
        await self._show(callback, ctx, ticket.client_telegram_id, _devices_screen)

    async def refund(self, callback: CallbackQuery, ticket: TicketRef, ctx: BotContext) -> None:
        """«↩️ Вернуть» — оплаты клиента за 30 дней (задача 21), дальше — `bot.admin_refunds`."""
        await self._show(callback, ctx, ticket.client_telegram_id, refund_list_screen)

    @staticmethod
    async def _show(
        callback: CallbackQuery,
        ctx: BotContext,
        telegram_id: int,
        screen: Callable[[BotContext, int], Awaitable[AdminScreen]],
    ) -> None:
        await callback.answer()
        bot = callback.bot
        if bot is None:
            raise RuntimeError("aiogram не передал бота в нажатие")
        try:
            await send_from(callback, bot, ctx, await screen(ctx, telegram_id))
        except PANEL_DOWN_ERRORS as error:
            logger.warning(
                "Карточка обращения, клиент %s: панель не ответила: %s", telegram_id, error
            )
            await send_from(
                callback, bot, ctx, _screens(ctx).panel_unavailable(ctx.alerter.explain(error))
            )
