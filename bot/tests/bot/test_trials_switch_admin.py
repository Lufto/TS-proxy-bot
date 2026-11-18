"""Выключатель пробных в боте (задача 28): `/trials` и кнопки в группе администраторов,
кнопки «Попробовать бесплатно» нет, пока пробные выключены, старая кнопка — честный экран."""

from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import Chat, InlineKeyboardMarkup, Update
from bot_updates import ADMIN_CHAT_ID, ADMIN_ID, CLIENT_ID, callback, message, user

from tsproxy.bot.admin_trials import AdminTrialsCallback
from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.context import BotContext
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.domain.constants import TRIAL_SPAM_LIMIT, TRIAL_SPAM_WINDOW
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.services.fakes import FakeAlertSender

ADMIN_GROUP = Chat(id=ADMIN_CHAT_ID, type="supergroup", title="Лично не публично", is_forum=True)
STRANGER_ID = 333
SPAM_MINUTES = int(TRIAL_SPAM_WINDOW.total_seconds()) // 60
DISABLE = AdminTrialsCallback(enable=False).pack()
ENABLE = AdminTrialsCallback(enable=True).pack()
TRIAL = MenuCallback(screen=ScreenId.TRIAL).pack()


def trials_command(*, sender_id: int = ADMIN_ID) -> Update:
    return message(user(sender_id, first_name="Админ"), "/trials", chat=ADMIN_GROUP)


def admin_press(data: str, *, sender_id: int = ADMIN_ID, chat_id: int = ADMIN_CHAT_ID) -> Update:
    return callback(user(sender_id, first_name="Админ"), data, chat_id=chat_id)


def texts_of(markup: object) -> list[str]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [b.text for row in markup.inline_keyboard for b in row]


def data_of(markup: object) -> list[str | None]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def state_text(content: Content, key: str) -> str:
    return content.admin_texts.get(key, limit=TRIAL_SPAM_LIMIT, minutes=SPAM_MINUTES)


async def test_trials_command_shows_state_and_switch_button(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await dispatcher.feed_update(bot, trials_command())

    [sent] = telegram.sent(SendMessage)
    assert sent.chat_id == ADMIN_CHAT_ID
    assert sent.text == state_text(content, "admin.trials.enabled")
    assert data_of(sent.reply_markup) == [DISABLE]


async def test_admin_switches_trials_off_and_on(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    ctx: BotContext,
    alert_sender: FakeAlertSender,
) -> None:
    await dispatcher.feed_update(bot, admin_press(DISABLE))

    assert not await ctx.trial_switch.enabled()
    [edited] = telegram.sent(EditMessageText)
    assert edited.text == "\n\n".join(
        [
            content.admin_texts.get("admin.trials.switched"),
            state_text(content, "admin.trials.disabled"),
        ]
    )
    assert texts_of(edited.reply_markup) == [content.admin_texts.get("admin.trials.enable_button")]
    assert len(alert_sender.sent) == 1
    telegram.requests.clear()

    await dispatcher.feed_update(bot, admin_press(ENABLE))

    assert await ctx.trial_switch.enabled()
    [edited] = telegram.sent(EditMessageText)
    assert data_of(edited.reply_markup) == [DISABLE]
    assert len(alert_sender.sent) == 2


async def test_second_press_says_nothing_changed(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await dispatcher.feed_update(bot, admin_press(DISABLE))
    telegram.requests.clear()

    await dispatcher.feed_update(bot, admin_press(DISABLE))

    [edited] = telegram.sent(EditMessageText)
    assert edited.text.startswith(content.admin_texts.get("admin.trials.unchanged"))


async def test_stranger_in_admin_group_cannot_use_trials(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    ctx: BotContext,
    content: Content,
) -> None:
    await dispatcher.feed_update(bot, trials_command(sender_id=STRANGER_ID))
    await dispatcher.feed_update(bot, admin_press(DISABLE, sender_id=STRANGER_ID))

    assert telegram.sent(SendMessage) == []
    assert telegram.sent(EditMessageText) == []
    [answer] = telegram.sent(AnswerCallbackQuery)
    assert answer.text == content.admin_texts.get("admin.not_admin")
    assert await ctx.trial_switch.enabled()


async def test_admin_outside_admin_group_cannot_switch(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, ctx: BotContext
) -> None:
    admin = user(ADMIN_ID, first_name="Админ")
    await dispatcher.feed_update(bot, admin_press(DISABLE, chat_id=-100777))
    await dispatcher.feed_update(bot, callback(admin, DISABLE))

    assert await ctx.trial_switch.enabled()
    assert telegram.sent(EditMessageText) == []


async def test_menu_connect_and_profile_hide_trial_while_off(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    ctx: BotContext,
) -> None:
    await ctx.trial_switch.disable(ADMIN_ID)
    client = user(CLIENT_ID)

    await dispatcher.feed_update(bot, message(client, "/start"))
    for screen in (ScreenId.MAIN, ScreenId.CONNECT, ScreenId.PROFILE):
        await dispatcher.feed_update(bot, callback(client, MenuCallback(screen=screen).pack()))

    [menu] = telegram.sent(SendMessage)
    screens = [menu.reply_markup, *(e.reply_markup for e in telegram.sent(EditMessageText))]
    assert len(screens) == 4
    trial_button = content.texts.get("buttons.trial")
    assert all(trial_button not in texts_of(markup) for markup in screens)


async def test_old_trial_button_while_off_offers_tariffs(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    ctx: BotContext,
    remnawave: FakeRemnawave,
) -> None:
    await ctx.trial_switch.disable(ADMIN_ID)

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), TRIAL))

    [edited] = telegram.sent(EditMessageText)
    assert edited.text == content.texts.get("trial.disabled")
    assert content.texts.get("buttons.tariffs") in texts_of(edited.reply_markup)
    assert remnawave.writes == []


async def test_trial_works_again_after_admin_turns_it_on(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    ctx: BotContext,
    remnawave: FakeRemnawave,
) -> None:
    await ctx.trial_switch.disable(ADMIN_ID)
    await ctx.trial_switch.enable(ADMIN_ID)

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), TRIAL))

    assert remnawave.writes == [("create", f"tg_{CLIENT_ID}")]
