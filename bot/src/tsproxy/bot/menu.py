"""Меню клиента: `/start`, `/help`, живое меню (экран на каждое нажатие), инструкции.

Ссылка `t.me/<бот>?start=support` (кнопки «Написать в поддержку» на сайте) открывает
сразу экран поддержки вместо меню (задача сайта 07), `?start=devices` (кнопка «Устройства»
на странице подписки) — «Мои устройства».

Над главным меню — строка статуса подписки (задача 19): «✅ Активна до …», «⛔ Закончилась»,
пробный — с датой. Срок знает только Remnawave, меню спрашивает панель (тот же расчёт
статуса, что в «Мой профиль»). Нет прокси-аккаунта, панель не ответила или думает дольше
`MENU_STATUS_TIMEOUT_SECONDS` — меню без строки, без ошибки и без тревоги.

Инструкция платформы (задача 17): шаг «откройте вашу страницу подключения» — личная ссылка
подписки нажавшего, её тоже даёт только панель (пока ждём — «печатает…»). Нет прокси-аккаунта,
панель не ответила или думает дольше `INSTRUCTIONS_LINK_TIMEOUT_SECONDS` — шаг «нажмите
„Подключиться“», ссылка на сайт остаётся; ошибки и тревоги нет.

Обработчики работают только в личном чате с ботом. Клиент всегда определяется по
`from_user.id` нажавшего, данные кнопки говорят только «какой экран открыть».

Возвращение после простоя (задача 26): нажатие кнопки на экране меню, отправленном
больше суток назад (`IDLE_RETURN_AFTER`), открывает главное меню вместо нажатого экрана;
нажатое сообщение убирается как обычно (`ClientDelivery.show`). Простой — по дате
нажатого сообщения: Telegram присылает её с нажатием, бот ничего не хранит.
Правило только для переходов по меню (`STALE_NAVIGATION`), чтобы клиент после перерыва
начинал с начала, а не с забытого глубокого экрана:
- кнопки экранов меню (`MenuCallback`), кроме «Подключиться», «Попробовать бесплатно»
  и «Тарифы»: это действия — ссылка, выдача пробного и покупка, ради них клиент и вернулся.
  Решаем по данным кнопки, а не только по метке сохраняемого текста: у уведомлений,
  отправленных до выкатки метки, её нет, а их «Продлить» и «Тарифы» ведут в тарифы;
- платформа в инструкциях, тариф в списке тарифов, частый вопрос в поддержке.
Не трогает действия, где главное меню вместо них потеряло бы намерение клиента: оплату
(СБП, звёзды, «Проверить оплату»), «Удалить» устройство, «Да, перевыпустить».
Не трогает и сообщения с сохраняемым текстом (`keeps_text`: уведомления, оплата, ответы
поддержки): их кнопки — призыв к действию из этого текста («Продлить», «В поддержку»).
Сообщение, недоступное боту (Telegram прислал его без даты), правило не трогает.
"""

import asyncio
import logging
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
from tsproxy.bot.chat_typing import TYPING
from tsproxy.bot.context import BotContext, profile_of
from tsproxy.bot.delivery import keeps_text
from tsproxy.bot.profile import devices_screen
from tsproxy.bot.profile_screens import ProfileScreens
from tsproxy.bot.screens import Screen
from tsproxy.db.models import Client
from tsproxy.integrations.remnawave_types import AccountNotFound, RemnawaveError
from tsproxy.services.clock import utc_now

logger = logging.getLogger(__name__)

# Параметры `/start` из ссылок сайта и страницы подписки: открыть поддержку, устройства
START_SUPPORT = "support"
START_DEVICES = "devices"
# `/help`: в личном чате — команды клиента, в группе администраторов — админки
HELP_COMMAND = "help"
# Сколько меню ждёт статус от панели: дольше — меню без строки статуса (задача 19)
MENU_STATUS_TIMEOUT_SECONDS = 3.0
# Сколько инструкция ждёт ссылку подписки от панели: дольше — шаг через «Подключиться» (задача 17)
INSTRUCTIONS_LINK_TIMEOUT_SECONDS = 3.0
# Простой, после которого старая кнопка меню открывает главное меню (задача 26)
IDLE_RETURN_AFTER = timedelta(days=1)
# Экраны меню, кнопки которых — действия, а не переходы: правило простоя их не трогает.
# «Тарифы» — ещё и «Продлить» уведомлений, в том числе старых, без метки сохраняемого текста
ACTION_SCREENS = frozenset({ScreenId.CONNECT, ScreenId.TRIAL, ScreenId.TARIFFS})
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
    # главное меню ждёт статус подписки от панели (до `MENU_STATUS_TIMEOUT_SECONDS`)
    router.message.register(on_start, CommandStart(), flags=TYPING)
    router.message.register(on_help, Command(HELP_COMMAND))
    # первым: роутер меню подключён раньше остальных экранов, переход сюда не дойдёт до них
    router.callback_query.register(on_main_menu, STALE_NAVIGATION, SentLongAgo(), flags=TYPING)
    router.callback_query.register(
        on_main_menu, MenuCallback.filter(F.screen == ScreenId.MAIN), flags=TYPING
    )
    router.callback_query.register(on_about, MenuCallback.filter(F.screen == ScreenId.ABOUT))
    router.callback_query.register(
        on_instructions, MenuCallback.filter(F.screen == ScreenId.INSTRUCTIONS)
    )
    router.callback_query.register(on_platform, InstructionsCallback.filter(), flags=TYPING)
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


async def _subscription_url(ctx: BotContext, telegram_id: int) -> str | None:
    """Личная ссылка подписки для инструкции (задача 17); `None` — шаг через «Подключиться».

    Нет прокси-аккаунта — обычная ситуация. Панель не ответила или не успела — тоже не ошибка:
    инструкция работает и без ссылки, тревогу шлют действия, которым панель нужна.
    """
    try:
        async with asyncio.timeout(INSTRUCTIONS_LINK_TIMEOUT_SECONDS):
            account = await ctx.remnawave.get_account(telegram_id)
    except AccountNotFound:
        return None
    except TimeoutError:
        logger.warning("Клиент %s: панель не дала ссылку для инструкции вовремя", telegram_id)
        return None
    except RemnawaveError as error:
        logger.warning("Клиент %s: ссылка для инструкции не получена: %s", telegram_id, error)
        return None
    return account.subscription_url


async def _main_menu(ctx: BotContext, client: Client) -> Screen:
    """Главное меню со строкой статуса. «Попробовать бесплатно» — пока пробные включены
    (задача 28) и клиенту пробный положен. Панель и база спрашиваются одновременно: меню
    ждёт дольшего из ответов, а не их сумму."""
    trials_enabled, trial_offered, status = await asyncio.gather(
        ctx.trial_switch.enabled(), ctx.clients.trial_offered(client), _status_line(ctx, client)
    )
    trial_available = trials_enabled and trial_offered
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
    url = await _subscription_url(ctx, callback.from_user.id)
    await _show(callback, ctx, ctx.screens.instructions(callback_data.platform, url))
