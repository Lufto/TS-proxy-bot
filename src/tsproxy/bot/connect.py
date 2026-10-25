"""«🚀 Подключиться»: страница подписки клиента и кнопка «📋 Скопировать ссылку».

Прокси-аккаунт ищется по Telegram ID нажавшего — чужую ссылку подменой кнопки не получить.
"""

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.context import BotContext, profile_of
from tsproxy.bot.screens import Screen
from tsproxy.integrations.remnawave_types import AccountNotFound, RemnawaveUnavailable

logger = logging.getLogger(__name__)

# Ключ склейки тревоги «панель недоступна» — общий для всего бота
REMNAWAVE_UNAVAILABLE_ALERT = "remnawave_unavailable"


async def panel_unavailable_screen(
    ctx: BotContext, telegram_id: int, error: RemnawaveUnavailable
) -> Screen:
    """Панель не ответила: тревога администраторам (склеивается), клиенту — «попробуйте позже»."""
    logger.warning("Панель недоступна для клиента %s: %s", telegram_id, error)
    await ctx.alerter.raise_alert(
        REMNAWAVE_UNAVAILABLE_ALERT, ctx.admin_texts.get("alerts.remnawave_unavailable")
    )
    return ctx.screens.unavailable()


async def connect_screen(ctx: BotContext, telegram_id: int) -> Screen:
    """Экран «Подключиться»: ссылка подписки, «подписки нет» или «панель недоступна»."""
    try:
        account = await ctx.remnawave.get_account(telegram_id)
    except AccountNotFound:
        # нормальная ситуация: клиент ещё не брал пробный и не покупал
        return ctx.screens.no_account(trial_available=await ctx.trial_switch.enabled())
    except RemnawaveUnavailable as error:
        return await panel_unavailable_screen(ctx, telegram_id, error)
    return ctx.screens.connect(account)


def create_connect_router() -> Router:
    router = Router(name="connect")
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.callback_query.register(on_connect, MenuCallback.filter(F.screen == ScreenId.CONNECT))
    return router


async def on_connect(callback: CallbackQuery, ctx: BotContext) -> None:
    # Отвечаем на нажатие сразу: панель может думать до нескольких секунд
    await callback.answer()
    await ctx.clients.resolve(profile_of(callback.from_user))
    screen = await connect_screen(ctx, callback.from_user.id)
    await ctx.delivery.show(callback, screen)
