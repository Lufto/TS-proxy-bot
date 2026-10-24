"""«👤 Мой профиль», `/profile` и «📱 Мои устройства» с «🗑 Удалить».

Клиент — всегда `from_user.id` нажавшего. Кнопка «🗑 Удалить» несёт только короткую
ссылку на устройство; сервис ищет его среди устройств нажавшего (аудит §4.3).
"""

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from tsproxy.bot.callbacks import DeviceCallback, MenuCallback, ScreenId
from tsproxy.bot.connect import REMNAWAVE_UNAVAILABLE_ALERT
from tsproxy.bot.context import BotContext, profile_of
from tsproxy.bot.profile_screens import ProfileScreens
from tsproxy.bot.screens import Screen
from tsproxy.db.models import Client
from tsproxy.integrations.remnawave_types import RemnawaveUnavailable

logger = logging.getLogger(__name__)

PROFILE_COMMAND = "profile"


def create_profile_router() -> Router:
    router = Router(name="profile")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.message.register(on_profile_command, Command(PROFILE_COMMAND))
    router.callback_query.register(on_profile, MenuCallback.filter(F.screen == ScreenId.PROFILE))
    router.callback_query.register(on_devices, MenuCallback.filter(F.screen == ScreenId.DEVICES))
    router.callback_query.register(on_delete_device, DeviceCallback.filter())
    return router


async def _unavailable(ctx: BotContext, telegram_id: int, error: RemnawaveUnavailable) -> Screen:
    """Панель не ответила: извиниться перед клиентом и поднять тревогу (склеивается)."""
    logger.warning("Профиль: панель недоступна для клиента %s: %s", telegram_id, error)
    await ctx.alerter.raise_alert(
        REMNAWAVE_UNAVAILABLE_ALERT, ctx.admin_texts.get("alerts.remnawave_unavailable")
    )
    return ctx.screens.unavailable()


async def profile_screen(ctx: BotContext, client: Client) -> Screen:
    """Экран профиля: статус и устройства, «подписки нет» или «панель недоступна»."""
    screens = ProfileScreens(ctx.texts)
    try:
        profile = await ctx.profiles.profile(client)
    except RemnawaveUnavailable as error:
        return await _unavailable(ctx, client.telegram_id, error)
    if profile is None:
        return screens.no_account(trial_available=client.trial_granted_at is None)
    return screens.profile(profile)


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


async def on_devices(callback: CallbackQuery, ctx: BotContext) -> None:
    await callback.answer()
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    screens = ProfileScreens(ctx.texts)
    try:
        devices = await ctx.profiles.devices(client.telegram_id)
    except RemnawaveUnavailable as error:
        screen = await _unavailable(ctx, client.telegram_id, error)
    else:
        if devices is None:
            screen = screens.no_account(trial_available=client.trial_granted_at is None)
        else:
            screen = screens.devices(devices)
    await ctx.delivery.show(callback, screen)


async def on_delete_device(
    callback: CallbackQuery, callback_data: DeviceCallback, ctx: BotContext
) -> None:
    """«🗑 Удалить»: только устройство из списка нажавшего; итог — над обновлённым списком."""
    await callback.answer()
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    screens = ProfileScreens(ctx.texts)
    try:
        result = await ctx.profiles.delete_device(client.telegram_id, callback_data.ref)
    except RemnawaveUnavailable as error:
        screen = await _unavailable(ctx, client.telegram_id, error)
    else:
        if result is None:
            screen = screens.no_account(trial_available=client.trial_granted_at is None)
        else:
            notice = "devices.deleted" if result.deleted else "devices.not_found"
            screen = screens.devices(result.devices, notice_key=notice)
    await ctx.delivery.show(callback, screen)
