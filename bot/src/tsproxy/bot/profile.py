"""«Мой профиль», `/profile`, «Мои устройства» с «Удалить» и «Перевыпустить ссылку».

Клиент — всегда `from_user.id` нажавшего. Кнопка «Удалить» несёт только короткую
ссылку на устройство; сервис ищет его среди устройств нажавшего (аудит §4.3).
Кнопки перевыпуска (задача 31) не несут ничего: перевыпускается только своя ссылка.
Раньше суток кнопка на профиле и устройствах серая (Bot API `disabled`), рядом — когда
снова можно; старая или подделанная кнопка получает отказ всплывашкой, экран не меняется.
"""

from datetime import datetime

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from tsproxy.bot.callbacks import DeviceCallback, MenuCallback, RevokeLinkCallback, ScreenId
from tsproxy.bot.chat_typing import TYPING
from tsproxy.bot.connect import PANEL_DOWN_ERRORS, panel_unavailable_screen
from tsproxy.bot.context import BotContext, profile_of
from tsproxy.bot.profile_screens import ProfileScreens
from tsproxy.bot.screens import Screen
from tsproxy.db.models import Client
from tsproxy.domain.dates import format_moscow
from tsproxy.services.link_revoke import LinkRevoked, LinkRevokeNoAccount, LinkRevokeTooSoon

PROFILE_COMMAND = "profile"


def create_profile_router() -> Router:
    router = Router(name="profile")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.message.register(on_profile_command, Command(PROFILE_COMMAND), flags=TYPING)
    router.callback_query.register(
        on_profile, MenuCallback.filter(F.screen == ScreenId.PROFILE), flags=TYPING
    )
    router.callback_query.register(
        on_devices, MenuCallback.filter(F.screen == ScreenId.DEVICES), flags=TYPING
    )
    router.callback_query.register(on_delete_device, DeviceCallback.filter(), flags=TYPING)
    router.callback_query.register(
        on_revoke_ask, MenuCallback.filter(F.screen == ScreenId.REVOKE_LINK)
    )
    router.callback_query.register(on_revoke, RevokeLinkCallback.filter(), flags=TYPING)
    return router


async def profile_screen(ctx: BotContext, client: Client) -> Screen:
    """Экран профиля: статус и устройства, «подписки нет» или «панель недоступна»."""
    screens = ProfileScreens(ctx.texts)
    try:
        profile = await ctx.profiles.profile(client)
    except PANEL_DOWN_ERRORS as error:
        return await panel_unavailable_screen(ctx, client.telegram_id, error, profile_screen)
    if profile is None:
        return await _no_account(ctx, screens)
    available_at = await ctx.link_revokes.available_at(client)
    return screens.profile(profile, revoke_available_at=available_at)


async def _no_account(ctx: BotContext, screens: ProfileScreens) -> Screen:
    """Подписки нет: пробный и тарифы; пробный — пока пробные включены (задача 28)."""
    return screens.no_account(trial_available=await ctx.trial_switch.enabled())


async def on_profile_command(message: Message, ctx: BotContext) -> None:
    """`/profile` — профиль новым сообщением."""
    if message.from_user is None:
        return
    client = await ctx.clients.resolve(profile_of(message.from_user))
    await ctx.delivery.send(message.from_user.id, await profile_screen(ctx, client))


async def on_profile(callback: CallbackQuery, ctx: BotContext) -> None:
    # Отвечаем на нажатие сразу: панель может думать до нескольких секунд
    await callback.answer()
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    await ctx.delivery.show(callback, await profile_screen(ctx, client))


async def devices_screen(ctx: BotContext, client: Client) -> Screen:
    """Экран «Мои устройства»: список, «подписки нет» или «панель недоступна»."""
    screens = ProfileScreens(ctx.texts)
    try:
        devices = await ctx.profiles.devices(client.telegram_id)
    except PANEL_DOWN_ERRORS as error:
        return await panel_unavailable_screen(ctx, client.telegram_id, error, devices_screen)
    if devices is None:
        return await _no_account(ctx, screens)
    available_at = await ctx.link_revokes.available_at(client)
    return screens.devices(devices, revoke_available_at=available_at)


async def on_devices(callback: CallbackQuery, ctx: BotContext) -> None:
    await callback.answer()
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    await ctx.delivery.show(callback, await devices_screen(ctx, client))


async def on_delete_device(
    callback: CallbackQuery, callback_data: DeviceCallback, ctx: BotContext
) -> None:
    """«Удалить»: только устройство из списка нажавшего; итог — над обновлённым списком."""
    await callback.answer()
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    screens = ProfileScreens(ctx.texts)
    try:
        result = await ctx.profiles.delete_device(client.telegram_id, callback_data.ref)
    except PANEL_DOWN_ERRORS as error:
        screen = await panel_unavailable_screen(ctx, client.telegram_id, error, on_delete_device)
    else:
        if result is None:
            screen = await _no_account(ctx, screens)
        else:
            notice = "devices.deleted" if result.deleted else "devices.not_found"
            available_at = await ctx.link_revokes.available_at(client)
            screen = screens.devices(
                result.devices, notice_key=notice, revoke_available_at=available_at
            )
    await ctx.delivery.show(callback, screen)


async def _refuse_too_soon(
    callback: CallbackQuery, ctx: BotContext, available_at: datetime
) -> None:
    """Раньше суток: всплывашка «можно будет …» над кнопкой, экран не меняется."""
    text = ctx.texts.get("revoke_link.too_soon", available_at=format_moscow(available_at))
    await ctx.delivery.answer(callback, alert=text)


async def on_revoke_ask(callback: CallbackQuery, ctx: BotContext) -> None:
    """«Перевыпустить ссылку»: предупреждение; раньше суток (старая кнопка) — всплывашка."""
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    available_at = await ctx.link_revokes.available_at(client)
    if available_at is not None:
        await _refuse_too_soon(callback, ctx, available_at)
        return
    await ctx.delivery.answer(callback)
    await ctx.delivery.show(callback, ProfileScreens(ctx.texts).revoke_ask())


async def on_revoke(callback: CallbackQuery, ctx: BotContext) -> None:
    """«Да, перевыпустить»: новая ссылка — экраном как в «Подключиться».

    На нажатие отвечаем после итога: отказ «раньше суток» — всплывашкой над кнопкой.
    """
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    screens = ProfileScreens(ctx.texts)
    try:
        result = await ctx.link_revokes.revoke(client)
    except PANEL_DOWN_ERRORS as error:
        screen = await panel_unavailable_screen(ctx, client.telegram_id, error, on_revoke)
    else:
        match result:
            case LinkRevoked(account):
                screen = ctx.screens.access_granted(ctx.texts.get("revoke_link.done"), account)
            case LinkRevokeTooSoon(available_at):
                await _refuse_too_soon(callback, ctx, available_at)
                return
            case LinkRevokeNoAccount():
                screen = screens.no_account(trial_available=await ctx.trial_switch.enabled())
    await ctx.delivery.answer(callback)
    await ctx.delivery.show(callback, screen)
