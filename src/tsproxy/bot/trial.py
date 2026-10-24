"""«🎁 Попробовать бесплатно»: выдача пробного периода и ссылка подписки (задача 07).

Клиент — только по `from_user.id` нажавшего. Повторное нажатие и гонку двух нажатий
отсекает `TrialService` (атомарная отметка в базе).
"""

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.connect import panel_unavailable_screen
from tsproxy.bot.context import BotContext, profile_of
from tsproxy.bot.screens import Screen, Screens
from tsproxy.integrations.remnawave_types import RemnawaveUnavailable
from tsproxy.services.trial import TrialOutcome, TrialResult


def create_trial_router() -> Router:
    router = Router(name="trial")
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.callback_query.register(on_trial, MenuCallback.filter(F.screen == ScreenId.TRIAL))
    return router


def trial_screen(screens: Screens, result: TrialResult) -> Screen:
    match result.outcome:
        case TrialOutcome.GRANTED if result.account is not None:
            return screens.trial_granted(result.account)
        case TrialOutcome.ALREADY_USED if result.account is None:
            return screens.trial_in_progress()
        case TrialOutcome.ALREADY_USED:
            return screens.trial_already_used()
        case TrialOutcome.HAS_SUBSCRIPTION:
            return screens.trial_has_subscription()
    raise ValueError(f"Неожиданный результат пробного: {result.outcome}")


async def on_trial(callback: CallbackQuery, ctx: BotContext) -> None:
    # Отвечаем на нажатие сразу: панель может думать до нескольких секунд
    await callback.answer()
    telegram_id = callback.from_user.id
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    try:
        result = await ctx.trials.start(client.id, telegram_id)
    except RemnawaveUnavailable as error:
        await ctx.delivery.show(callback, await panel_unavailable_screen(ctx, telegram_id, error))
        return
    await ctx.delivery.show(callback, trial_screen(ctx.screens, result))
