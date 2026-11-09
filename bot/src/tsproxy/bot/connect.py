"""«🚀 Подключиться»: страница подписки клиента и кнопка «📋 Скопировать ссылку».

Прокси-аккаунт ищется по Telegram ID нажавшего — чужую ссылку подменой кнопки не получить.

Панель не ответила — клиенту «пришлём ссылку сюда», и фоновая задача `deliver_pending_connects`
присылает экран подключения, когда панель ожила (владелец, 2026-11-10; `services.connect_waits`).
"""

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.context import BotContext, profile_of
from tsproxy.bot.screens import Screen
from tsproxy.db.models import Client
from tsproxy.integrations.remnawave_types import (
    AccountNotFound,
    RemnawaveAuthError,
    RemnawaveUnavailable,
)
from tsproxy.services.connect_waits import DeliveredBy

logger = logging.getLogger(__name__)

# Ключ склейки тревоги «панель недоступна» — общий для всего бота
REMNAWAVE_UNAVAILABLE_ALERT = "remnawave_unavailable"
# Панель не ответила или не приняла токен: клиенту одно и то же — «пришлём ссылку»
PANEL_DOWN_ERRORS = (RemnawaveUnavailable, RemnawaveAuthError)


async def panel_unavailable_screen(
    ctx: BotContext, telegram_id: int, error: RemnawaveUnavailable
) -> Screen:
    """Панель не ответила: тревога администраторам (склеивается), клиенту — «попробуйте позже»."""
    logger.warning("Панель недоступна для клиента %s: %s", telegram_id, error)
    await ctx.alerter.raise_alert(
        REMNAWAVE_UNAVAILABLE_ALERT, ctx.admin_texts.get("alerts.remnawave_unavailable")
    )
    return ctx.screens.unavailable()


async def connect_screen(ctx: BotContext, client: Client) -> Screen:
    """Экран «Подключиться»: ссылка подписки, «подписки нет» или «пришлём ссылку, когда панель
    ответит»."""
    try:
        screen = await _account_screen(ctx, client.telegram_id)
    except PANEL_DOWN_ERRORS as error:
        logger.warning("Панель недоступна для клиента %s: %s", client.telegram_id, error)
        await ctx.alerter.alert(REMNAWAVE_UNAVAILABLE_ALERT, source=connect_screen, error=error)
        await ctx.connect_waits.wait(client.id)
        return ctx.screens.connect_pending()
    await ctx.connect_waits.delivered(client.id, DeliveredBy.CLIENT)
    return screen


async def _account_screen(ctx: BotContext, telegram_id: int) -> Screen:
    """Ссылка подписки или «подписки нет». Панель не ответила — `PANEL_DOWN_ERRORS`."""
    try:
        account = await ctx.remnawave.get_account(telegram_id)
    except AccountNotFound:
        # нормальная ситуация: клиент ещё не брал пробный и не покупал
        return ctx.screens.no_account(trial_available=await ctx.trial_switch.enabled())
    return ctx.screens.connect(account)


async def deliver_pending_connects(ctx: BotContext) -> None:
    """Фоновая задача: прислать экран подключения тем, кому панель не ответила."""
    for telegram_id in await ctx.connect_waits.waiting():
        try:
            screen = await _account_screen(ctx, telegram_id)
        except PANEL_DOWN_ERRORS:
            return  # панель всё ещё не отвечает — следующий проход через минуту
        client = await ctx.clients.find(telegram_id)
        if client is None:
            continue
        # Отмечаем до отправки: сбой Telegram не должен слать ссылку каждую минуту
        await ctx.connect_waits.delivered(client.id, DeliveredBy.BOT)
        try:
            await ctx.delivery.send(telegram_id, screen)
        except TelegramAPIError:
            logger.exception("Не удалось прислать ссылку клиенту %s", telegram_id)


def create_connect_router() -> Router:
    router = Router(name="connect")
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.callback_query.register(on_connect, MenuCallback.filter(F.screen == ScreenId.CONNECT))
    return router


async def on_connect(callback: CallbackQuery, ctx: BotContext) -> None:
    # Отвечаем на нажатие сразу: панель может думать до нескольких секунд
    await callback.answer()
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    screen = await connect_screen(ctx, client)
    await ctx.delivery.show(callback, screen)
