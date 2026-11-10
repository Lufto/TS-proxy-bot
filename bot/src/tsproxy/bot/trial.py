"""«🎁 Попробовать бесплатно»: выдача пробного периода и ссылка подписки (задача 07).

Клиент — только по `from_user.id` нажавшего. Повторное нажатие и гонку двух нажатий
отсекает `TrialService` (атомарная отметка в базе). Панель не ответила — пробный за
клиентом: фоновая задача `deliver_pending_trials` выдаст его и пришлёт ссылку сама.
"""

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.connect import REMNAWAVE_UNAVAILABLE_ALERT
from tsproxy.bot.context import BotContext, profile_of
from tsproxy.bot.screens import Screen, Screens
from tsproxy.services.trial import TrialOutcome, TrialResult

logger = logging.getLogger(__name__)

REMNAWAVE_TRIAL_ALERT = "remnawave_trial"


def create_trial_router() -> Router:
    router = Router(name="trial")
    router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    router.callback_query.register(on_trial, MenuCallback.filter(F.screen == ScreenId.TRIAL))
    return router


def trial_screen(screens: Screens, result: TrialResult) -> Screen | None:
    """Экран по итогу нажатия. `None` — сообщение не трогаем: его обновит другое нажатие."""
    match result.outcome:
        case TrialOutcome.GRANTED if result.account is not None:
            return screens.trial_granted(result.account)
        case TrialOutcome.ALREADY_USED:
            return screens.trial_already_used()
        case TrialOutcome.HAS_SUBSCRIPTION:
            return screens.trial_has_subscription()
        case TrialOutcome.PENDING:
            return screens.trial_pending()
        case TrialOutcome.DISABLED:
            return screens.trial_disabled()
        case TrialOutcome.IN_PROGRESS:
            return None
    raise ValueError(f"Неожиданный результат пробного: {result.outcome}")


async def on_trial(callback: CallbackQuery, ctx: BotContext) -> None:
    # Отвечаем на нажатие сразу: панель может думать до нескольких секунд
    await callback.answer()
    client = await ctx.clients.resolve(profile_of(callback.from_user))
    result = await ctx.trials.start(client.id, callback.from_user.id)
    if result.outcome is TrialOutcome.PENDING:
        await ctx.alerter.alert(
            REMNAWAVE_TRIAL_ALERT,
            source=on_trial,
            error=result.error,
            glue=REMNAWAVE_UNAVAILABLE_ALERT,
        )
    screen = trial_screen(ctx.screens, result)
    if screen is not None:
        await ctx.delivery.show(callback, screen)


async def deliver_pending_trials(ctx: BotContext) -> None:
    """Фоновая задача: выдать пробные, которые не прошли из-за панели, и прислать ссылку."""
    for delivered in await ctx.trials.deliver_pending():
        try:
            await ctx.delivery.send(
                delivered.telegram_id, ctx.screens.trial_granted(delivered.account)
            )
        # Пробный уже выдан: ссылка есть в «🚀 Подключиться». Один сбой Telegram не должен
        # оставить без сообщения остальных клиентов из этого прохода.
        except TelegramAPIError:
            logger.exception(
                "Не удалось прислать ссылку пробного клиенту %s", delivered.telegram_id
            )
