"""Меню клиента: `/start`, команды, переключение экранов в одном сообщении, инструкции.

Обработчики работают только в личном чате с ботом. Клиент всегда определяется по
`from_user.id` нажавшего, данные кнопки говорят только «какой экран открыть».
"""

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from tsproxy.bot.callbacks import InstructionsCallback, MenuCallback, ScreenId
from tsproxy.bot.context import BotContext, profile_of
from tsproxy.bot.screens import PLACEHOLDER_TEXTS, Screen

# Команды бота → экраны (описания команд — `commands.*` в texts.yaml).
# `/support` — в `bot.support` (SUPPORT_COMMAND).
COMMAND_SCREENS: dict[str, ScreenId] = {
    "profile": ScreenId.PROFILE,
    "buy": ScreenId.TARIFFS,
}


def create_menu_router() -> Router:
    """Роутер меню. Каждый вызов — новый роутер (aiogram подключает роутер только один раз)."""
    router = Router(name="menu")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.message.register(on_start, CommandStart())
    router.message.register(on_screen_command, Command(*COMMAND_SCREENS))
    router.callback_query.register(on_main_menu, MenuCallback.filter(F.screen == ScreenId.MAIN))
    router.callback_query.register(
        on_placeholder, MenuCallback.filter(F.screen.in_(PLACEHOLDER_TEXTS.keys()))
    )
    router.callback_query.register(on_about, MenuCallback.filter(F.screen == ScreenId.ABOUT))
    router.callback_query.register(
        on_instructions, MenuCallback.filter(F.screen == ScreenId.INSTRUCTIONS)
    )
    router.callback_query.register(on_platform, InstructionsCallback.filter())
    return router


async def _show(callback: CallbackQuery, ctx: BotContext, screen: Screen) -> None:
    await ctx.delivery.show(callback, screen)


async def on_start(message: Message, ctx: BotContext) -> None:
    """`/start`: записать клиента (или обновить имя), снять «заблокировал», прислать меню."""
    if message.from_user is None:
        return
    client = await ctx.clients.register(profile_of(message.from_user))
    screen = ctx.screens.main_menu(trial_available=client.trial_granted_at is None)
    await ctx.delivery.send(message.from_user.id, screen)


async def on_screen_command(message: Message, command: CommandObject, ctx: BotContext) -> None:
    """`/profile`, `/buy` — те же экраны, что и кнопки меню, новым сообщением."""
    if message.from_user is None:
        return
    await ctx.clients.resolve(profile_of(message.from_user))
    screen = ctx.screens.placeholder(COMMAND_SCREENS[command.command.lower()])
    await ctx.delivery.send(message.from_user.id, screen)


async def on_main_menu(callback: CallbackQuery, ctx: BotContext) -> None:
    await callback.answer()
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    screen = ctx.screens.main_menu(trial_available=client.trial_granted_at is None)
    await _show(callback, ctx, screen)


async def on_placeholder(
    callback: CallbackQuery, callback_data: MenuCallback, ctx: BotContext
) -> None:
    """Экраны следующих задач: пробный (07), тарифы (12), профиль (08)."""
    await callback.answer()
    await _show(callback, ctx, ctx.screens.placeholder(callback_data.screen))


async def on_about(callback: CallbackQuery, ctx: BotContext) -> None:
    await callback.answer()
    await _show(callback, ctx, ctx.screens.about())


async def on_instructions(callback: CallbackQuery, ctx: BotContext) -> None:
    await callback.answer()
    await _show(callback, ctx, ctx.screens.instructions_menu())


async def on_platform(
    callback: CallbackQuery, callback_data: InstructionsCallback, ctx: BotContext
) -> None:
    await callback.answer()
    await _show(callback, ctx, ctx.screens.instructions(callback_data.platform))
