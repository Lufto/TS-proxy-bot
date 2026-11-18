"""Меню клиента: `/start`, `/help`, живое меню (экран на каждое нажатие), инструкции.

Ссылка `t.me/<бот>?start=support` (кнопки «Написать в поддержку» на сайте) открывает
сразу экран поддержки вместо меню (задача сайта 07), `?start=devices` (кнопка «Устройства»
на странице подписки) — «Мои устройства».

Над главным меню — строка статуса подписки (задача 19): «✅ Активна до …», «⛔ Закончилась»,
пробный — с датой. Срок знает только Remnawave, меню спрашивает панель (тот же расчёт
статуса, что в «Мой профиль»). Нет прокси-аккаунта, панель не ответила или думает дольше
`MENU_STATUS_TIMEOUT_SECONDS` — меню без строки, без ошибки и без тревоги.

Обработчики работают только в личном чате с ботом. Клиент всегда определяется по
`from_user.id` нажавшего, данные кнопки говорят только «какой экран открыть».
"""

import asyncio
import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from tsproxy.bot.callbacks import InstructionsCallback, MenuCallback, ScreenId
from tsproxy.bot.context import BotContext, profile_of
from tsproxy.bot.profile import devices_screen
from tsproxy.bot.profile_screens import ProfileScreens
from tsproxy.bot.screens import Screen
from tsproxy.db.models import Client
from tsproxy.integrations.remnawave_types import RemnawaveError

logger = logging.getLogger(__name__)

# Параметры `/start` из ссылок сайта и страницы подписки: открыть поддержку, устройства
START_SUPPORT = "support"
START_DEVICES = "devices"
# `/help`: в личном чате — команды клиента, в группе администраторов — админки
HELP_COMMAND = "help"
# Сколько меню ждёт статус от панели: дольше — меню без строки статуса (задача 19)
MENU_STATUS_TIMEOUT_SECONDS = 3.0


def create_menu_router() -> Router:
    """Роутер меню. Каждый вызов — новый роутер (aiogram подключает роутер только один раз)."""
    router = Router(name="menu")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.message.register(on_start, CommandStart())
    router.message.register(on_help, Command(HELP_COMMAND))
    router.callback_query.register(on_main_menu, MenuCallback.filter(F.screen == ScreenId.MAIN))
    router.callback_query.register(on_about, MenuCallback.filter(F.screen == ScreenId.ABOUT))
    router.callback_query.register(
        on_instructions, MenuCallback.filter(F.screen == ScreenId.INSTRUCTIONS)
    )
    router.callback_query.register(on_platform, InstructionsCallback.filter())
    return router


async def _show(callback: CallbackQuery, ctx: BotContext, screen: Screen) -> None:
    await ctx.delivery.show(callback, screen)


async def _status_line(ctx: BotContext, client: Client) -> str | None:
    """Строка статуса подписки (задача 19). Срок знает только панель — спрашиваем её.

    Нет прокси-аккаунта, панель не ответила или думает дольше
    `MENU_STATUS_TIMEOUT_SECONDS` — меню без строки: строка не важнее самого меню.
    Тревогу «панель не отвечает» здесь не шлём — её шлют действия, которым панель нужна.
    """
    try:
        async with asyncio.timeout(MENU_STATUS_TIMEOUT_SECONDS):
            status = await ctx.profiles.status(client)
    except TimeoutError:
        logger.warning("Клиент %s: панель не дала статус для меню вовремя", client.telegram_id)
        return None
    except RemnawaveError as error:
        logger.warning("Клиент %s: статус для меню не получен: %s", client.telegram_id, error)
        return None
    if status is None:
        return None
    return ProfileScreens(ctx.texts).menu_status(status)


async def _main_menu(ctx: BotContext, client: Client) -> Screen:
    """Главное меню со строкой статуса. «Попробовать бесплатно» — пока пробные включены
    (задача 28) и клиенту пробный положен."""
    trial_available = await ctx.trial_switch.enabled() and await ctx.clients.trial_offered(client)
    status = await _status_line(ctx, client)
    return ctx.screens.main_menu(trial_available=trial_available, status=status)


async def on_start(message: Message, command: CommandObject, ctx: BotContext) -> None:
    """`/start`: записать клиента (или обновить имя), снять «заблокировал», прислать меню.

    С параметром `support` вместо меню — экран поддержки, с `devices` — устройства;
    другие параметры не важны.
    """
    if message.from_user is None:
        return
    client = await ctx.clients.register(profile_of(message.from_user))
    if command.args == START_SUPPORT:
        await ctx.delivery.send(message.from_user.id, ctx.screens.support())
        return
    if command.args == START_DEVICES:
        await ctx.delivery.send(message.from_user.id, await devices_screen(ctx, client))
        return
    await ctx.delivery.send(message.from_user.id, await _main_menu(ctx, client))


async def on_help(message: Message, ctx: BotContext) -> None:
    """`/help` — список команд клиента новым сообщением."""
    if message.from_user is None:
        return
    await ctx.delivery.send(message.from_user.id, ctx.screens.help())


async def on_main_menu(callback: CallbackQuery, ctx: BotContext) -> None:
    await callback.answer()
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    await _show(callback, ctx, await _main_menu(ctx, client))


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
