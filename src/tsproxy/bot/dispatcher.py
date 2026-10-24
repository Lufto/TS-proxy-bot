"""Сборка диспетчера aiogram: роутеры, ограничение частоты, обработчик ошибок."""

from collections.abc import Sequence

from aiogram import Dispatcher, Router
from aiogram.types import BotCommand

from tsproxy.bot.admin import ForeignChatFilter
from tsproxy.bot.chats import create_chats_router
from tsproxy.bot.connect import create_connect_router
from tsproxy.bot.context import CONTEXT_KEY, BotContext
from tsproxy.bot.errors import on_error
from tsproxy.bot.fallback import create_fallback_router
from tsproxy.bot.menu import COMMAND_SCREENS, create_menu_router
from tsproxy.bot.profile import create_profile_router
from tsproxy.bot.throttling import RateLimiter, ThrottlingMiddleware
from tsproxy.bot.trial import create_trial_router
from tsproxy.content.loader import Texts


def create_dispatcher(
    ctx: BotContext,
    limiter: RateLimiter,
    extra_routers: Sequence[Router] = (),
) -> Dispatcher:
    """Диспетчер со всеми обработчиками.

    `extra_routers` (админка, поддержка — следующие задачи) подключаются перед
    «запасным» роутером, который отвечает на всё, что не подошло остальным.
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
        *extra_routers,
        create_fallback_router(),
    )
    dispatcher.errors.register(on_error)
    return dispatcher


def bot_commands(texts: Texts) -> list[BotCommand]:
    """Команды в меню Telegram (кнопка «/» рядом с полем ввода)."""
    names = ["start", *COMMAND_SCREENS]
    return [BotCommand(command=name, description=texts.get(f"commands.{name}")) for name in names]
