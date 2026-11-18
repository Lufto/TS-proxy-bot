"""Меню клиента: `/start`, `/help`, живое меню (экран на каждое нажатие), инструкции.

Ссылка `t.me/<бот>?start=support` (кнопки «Написать в поддержку» на сайте) открывает
сразу экран поддержки вместо меню (задача сайта 07), `?start=devices` (кнопка «Устройства»
на странице подписки) — «Мои устройства».

Обработчики работают только в личном чате с ботом. Клиент всегда определяется по
`from_user.id` нажавшего, данные кнопки говорят только «какой экран открыть».

Возвращение после простоя (задача 26): нажатие кнопки на экране меню, отправленном
больше суток назад (`IDLE_RETURN_AFTER`), открывает главное меню вместо нажатого экрана;
нажатое сообщение убирается как обычно (`ClientDelivery.show`). Простой — по дате
нажатого сообщения: Telegram присылает её с нажатием, бот ничего не хранит.
Правило только для переходов по меню (`STALE_NAVIGATION`), чтобы клиент после перерыва
начинал с начала, а не с забытого глубокого экрана:
- кнопки экранов меню (`MenuCallback`), кроме «Подключиться» и «Попробовать бесплатно»:
  это действия — ссылка и выдача пробного, ради них клиент и вернулся;
- платформа в инструкциях, тариф в списке тарифов, частый вопрос в поддержке.
Не трогает действия, где главное меню вместо них потеряло бы намерение клиента: оплату
(СБП, звёзды, «Проверить оплату»), «Удалить» устройство, «Да, перевыпустить».
Не трогает и сообщения с сохраняемым текстом (`keeps_text`: уведомления, оплата, ответы
поддержки): их кнопки — призыв к действию из этого текста («Продлить», «В поддержку»).
Сообщение, недоступное боту (Telegram прислал его без даты), правило не трогает.
"""

from datetime import timedelta

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart, Filter, or_f
from aiogram.types import CallbackQuery, Message

from tsproxy.bot.callbacks import (
    FaqCallback,
    InstructionsCallback,
    MenuCallback,
    ScreenId,
    TariffScreenCallback,
)
from tsproxy.bot.context import BotContext, profile_of
from tsproxy.bot.delivery import keeps_text
from tsproxy.bot.profile import devices_screen
from tsproxy.bot.screens import Screen
from tsproxy.db.models import Client
from tsproxy.services.clock import utc_now

# Параметры `/start` из ссылок сайта и страницы подписки: открыть поддержку, устройства
START_SUPPORT = "support"
START_DEVICES = "devices"
# `/help`: в личном чате — команды клиента, в группе администраторов — админки
HELP_COMMAND = "help"
# Простой, после которого старая кнопка меню открывает главное меню (задача 26)
IDLE_RETURN_AFTER = timedelta(days=1)
# Экраны меню, кнопки которых — действия, а не переходы: правило простоя их не трогает
ACTION_SCREENS = frozenset({ScreenId.CONNECT, ScreenId.TRIAL})
# Переходы по меню, к которым применяется правило простоя
STALE_NAVIGATION = or_f(
    MenuCallback.filter(~F.screen.in_(ACTION_SCREENS)),
    InstructionsCallback.filter(),
    TariffScreenCallback.filter(),
    FaqCallback.filter(),
)


class SentLongAgo(Filter):
    """Нажатое сообщение — экран меню, отправленный больше `IDLE_RETURN_AFTER` назад."""

    async def __call__(self, callback: CallbackQuery, ctx: BotContext) -> bool:
        pressed = callback.message
        if not isinstance(pressed, Message) or keeps_text(pressed):
            return False
        return utc_now(ctx.clock) - pressed.date > IDLE_RETURN_AFTER


def create_menu_router() -> Router:
    """Роутер меню. Каждый вызов — новый роутер (aiogram подключает роутер только один раз)."""
    router = Router(name="menu")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.message.register(on_start, CommandStart())
    router.message.register(on_help, Command(HELP_COMMAND))
    # первым: роутер меню подключён раньше остальных экранов, переход сюда не дойдёт до них
    router.callback_query.register(on_main_menu, STALE_NAVIGATION, SentLongAgo())
    router.callback_query.register(on_main_menu, MenuCallback.filter(F.screen == ScreenId.MAIN))
    router.callback_query.register(on_about, MenuCallback.filter(F.screen == ScreenId.ABOUT))
    router.callback_query.register(
        on_instructions, MenuCallback.filter(F.screen == ScreenId.INSTRUCTIONS)
    )
    router.callback_query.register(on_platform, InstructionsCallback.filter())
    return router


async def _show(callback: CallbackQuery, ctx: BotContext, screen: Screen) -> None:
    await ctx.delivery.show(callback, screen)


async def _main_menu(ctx: BotContext, client: Client) -> Screen:
    """Главное меню. «Попробовать бесплатно» — пока пробные включены (задача 28)
    и клиенту пробный положен."""
    trial_available = await ctx.trial_switch.enabled() and await ctx.clients.trial_offered(client)
    return ctx.screens.main_menu(trial_available=trial_available)


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
