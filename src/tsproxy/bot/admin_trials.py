"""`/trials` в группе администраторов: включены ли пробные и кнопка переключения (задача 28).

«⏸ Выключить пробные» / «▶️ Включить пробные» — сразу, без подтверждения: действие обратимо
одной кнопкой. Журнал и сообщение в «🚨 Тревоги» пишет `TrialSwitch`.

Права — как у всей админки: `AdminFilter` на каждом обработчике (администратор и группа
администраторов). Команду от остальных роутер молча не берёт; кнопку — всплывающий отказ.
"""

from aiogram import Bot, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from tsproxy.bot.admin import AdminAccess, AdminFilter
from tsproxy.bot.admin_clients import on_foreign_press
from tsproxy.bot.admin_screens import AdminScreen
from tsproxy.bot.context import BotContext
from tsproxy.content.loader import Texts
from tsproxy.domain.constants import TRIAL_SPAM_LIMIT, TRIAL_SPAM_WINDOW

TRIALS_COMMAND = "trials"
SECONDS_PER_MINUTE = 60
PARAGRAPH = "\n\n"


class AdminTrialsCallback(CallbackData, prefix="admt"):
    """`enable=True` — «▶️ Включить пробные», `False` — «⏸ Выключить пробные»."""

    enable: bool


def create_admin_trials_router(access: AdminAccess) -> Router:
    router = Router(name="admin_trials")
    admin = AdminFilter(access)
    router.message.register(on_trials_command, admin, Command(TRIALS_COMMAND))
    router.callback_query.register(on_trials_switch, admin, AdminTrialsCallback.filter())
    # Сюда доходят только нажатия, которые не прошли `AdminFilter`
    router.callback_query.register(on_foreign_press, AdminTrialsCallback.filter())
    return router


def trials_screen(texts: Texts, *, enabled: bool, notice_key: str | None = None) -> AdminScreen:
    """Состояние пробных и одна кнопка — переключить. `notice_key` — итог нажатия сверху."""
    state_key = "admin.trials.enabled" if enabled else "admin.trials.disabled"
    state = texts.get(
        state_key,
        limit=TRIAL_SPAM_LIMIT,
        minutes=int(TRIAL_SPAM_WINDOW.total_seconds()) // SECONDS_PER_MINUTE,
    )
    button_key = "admin.trials.disable_button" if enabled else "admin.trials.enable_button"
    button = InlineKeyboardButton(
        text=texts.get(button_key), callback_data=AdminTrialsCallback(enable=not enabled).pack()
    )
    notice = [] if notice_key is None else [texts.get(notice_key)]
    return AdminScreen(
        PARAGRAPH.join([*notice, state]), InlineKeyboardMarkup(inline_keyboard=[[button]])
    )


async def on_trials_command(message: Message, ctx: BotContext) -> None:
    screen = trials_screen(ctx.admin_texts, enabled=await ctx.trial_switch.enabled())
    await message.answer(screen.text, reply_markup=screen.markup)


async def on_trials_switch(
    callback: CallbackQuery, callback_data: AdminTrialsCallback, bot: Bot, ctx: BotContext
) -> None:
    await callback.answer()
    switch = ctx.trial_switch
    admin_id = callback.from_user.id
    if callback_data.enable:
        changed = await switch.enable(admin_id)
    else:
        changed = await switch.disable(admin_id)
    screen = trials_screen(
        ctx.admin_texts,
        enabled=await switch.enabled(),
        notice_key="admin.trials.switched" if changed else "admin.trials.unchanged",
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(screen.text, reply_markup=screen.markup)
        return
    # Сообщение слишком старое (Telegram его не отдаёт) — пишем в группу без темы
    await bot.send_message(ctx.access.admin_chat_id, screen.text, reply_markup=screen.markup)
