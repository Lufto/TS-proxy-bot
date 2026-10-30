"""Сборка диспетчера aiogram: роутеры, ограничение частоты, обработчик ошибок."""

from collections.abc import Sequence

from aiogram import Dispatcher, Router
from aiogram.types import BotCommand

from tsproxy.bot.admin import ForeignChatFilter
from tsproxy.bot.admin_clients import AdminCardClientActions, create_admin_router
from tsproxy.bot.admin_refunds import create_admin_refunds_router
from tsproxy.bot.admin_trials import create_admin_trials_router
from tsproxy.bot.chats import create_chats_router
from tsproxy.bot.connect import create_connect_router
from tsproxy.bot.context import CONTEXT_KEY, BotContext
from tsproxy.bot.errors import on_error
from tsproxy.bot.fallback import create_fallback_router
from tsproxy.bot.menu import create_menu_router
from tsproxy.bot.profile import PROFILE_COMMAND, create_profile_router
from tsproxy.bot.refunds import create_refunds_router
from tsproxy.bot.stars import create_stars_router
from tsproxy.bot.support import PAYSUPPORT_COMMAND, SUPPORT_COMMAND, create_support_router
from tsproxy.bot.tariffs import BUY_COMMAND, create_tariffs_router
from tsproxy.bot.throttling import RateLimiter, ThrottlingMiddleware
from tsproxy.bot.trial import create_trial_router
from tsproxy.content.loader import Texts


def create_dispatcher(
    ctx: BotContext,
    limiter: RateLimiter,
    extra_routers: Sequence[Router] = (),
) -> Dispatcher:
    """Диспетчер со всеми обработчиками.

    `extra_routers` (админка и другие следующие задачи) подключаются перед поддержкой:
    поддержка берёт все обычные сообщения клиента, а «запасной» роутер — остальное.
    """
    dispatcher = Dispatcher()
    dispatcher[CONTEXT_KEY] = ctx
    throttling = ThrottlingMiddleware(limiter, ctx.texts, ctx.access.admin_ids)
    dispatcher.message.outer_middleware(throttling)
    dispatcher.callback_query.outer_middleware(throttling)
    dispatcher.include_routers(
        create_chats_router(ForeignChatFilter(ctx.access)),
        create_menu_router(),
        create_connect_router(),
        create_trial_router(),
        create_profile_router(),
        create_tariffs_router(),
        create_stars_router(),  # задача 33: раньше поддержки — она берёт все сообщения
        create_refunds_router(),  # задача 21: случайная вторая оплата
        create_admin_router(ctx.access),  # задача 18
        create_admin_trials_router(ctx.access),  # задача 28
        create_admin_refunds_router(ctx.access),  # задача 21
        *extra_routers,
        create_support_router(ctx.access, client_actions=AdminCardClientActions()),
        create_fallback_router(),
    )
    dispatcher.errors.register(on_error)
    return dispatcher


def bot_commands(texts: Texts) -> list[BotCommand]:
    """Команды в меню Telegram (кнопка «/» рядом с полем ввода)."""
    names = ["start", PROFILE_COMMAND, BUY_COMMAND, SUPPORT_COMMAND, PAYSUPPORT_COMMAND]
    return [BotCommand(command=name, description=texts.get(f"commands.{name}")) for name in names]
