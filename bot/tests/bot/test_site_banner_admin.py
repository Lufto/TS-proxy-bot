"""Плашка на сайте в боте (задачи сайта 14, 15): `/banner` и кнопки в группе
администраторов, кнопка «Поставить плашку» под тревогой «Remnawave не отвечает»."""

from aiogram import Bot, Dispatcher
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import Chat, InlineKeyboardMarkup, Update
from bot_updates import ADMIN_CHAT_ID, ADMIN_ID, callback, message, user

from tsproxy.bot.admin_site_banner import AdminSiteBannerCallback, banner_alert_button
from tsproxy.bot.connect import REMNAWAVE_UNAVAILABLE_ALERT
from tsproxy.bot.context import BotContext
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.domain.site_banner import SiteBanner
from tsproxy.services.fakes import FakeAlertSender

ADMIN_GROUP = Chat(id=ADMIN_CHAT_ID, type="supergroup", title="Лично не публично", is_forum=True)
STRANGER_ID = 333
MAINTENANCE = AdminSiteBannerCallback(banner=SiteBanner.MAINTENANCE).pack()
PROXY_ISSUES = AdminSiteBannerCallback(banner=SiteBanner.PROXY_ISSUES).pack()
REMOVE = AdminSiteBannerCallback(banner=SiteBanner.NONE).pack()


def banner_command(*, sender_id: int = ADMIN_ID) -> Update:
    return message(user(sender_id, first_name="Админ"), "/banner", chat=ADMIN_GROUP)


def admin_press(data: str, *, sender_id: int = ADMIN_ID) -> Update:
    return callback(user(sender_id, first_name="Админ"), data, chat_id=ADMIN_CHAT_ID)


def data_of(markup: object) -> list[str | None]:
    assert isinstance(markup, InlineKeyboardMarkup)
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def state_text(content: Content, banner: SiteBanner) -> str:
    name = content.admin_texts.get(f"admin.site_banner.names.{banner.value}")
    return content.admin_texts.get("admin.site_banner.state", banner=name)


async def test_banner_command_shows_state_and_buttons(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await dispatcher.feed_update(bot, banner_command())

    [sent] = telegram.sent(SendMessage)
    assert sent.chat_id == ADMIN_CHAT_ID
    assert sent.text == state_text(content, SiteBanner.NONE)
    assert data_of(sent.reply_markup) == [MAINTENANCE, PROXY_ISSUES]


async def test_admin_sets_and_removes_banner(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    ctx: BotContext,
) -> None:
    await dispatcher.feed_update(bot, admin_press(MAINTENANCE))

    assert await ctx.site_banner.current() is SiteBanner.MAINTENANCE
    [edited] = telegram.sent(EditMessageText)
    assert edited.text == "\n\n".join(
        [
            content.admin_texts.get("admin.site_banner.switched"),
            state_text(content, SiteBanner.MAINTENANCE),
        ]
    )
    assert data_of(edited.reply_markup) == [REMOVE, PROXY_ISSUES]
    telegram.requests.clear()

    await dispatcher.feed_update(bot, admin_press(REMOVE))

    assert await ctx.site_banner.current() is SiteBanner.NONE


async def test_stranger_cannot_set_banner(
    dispatcher: Dispatcher, bot: Bot, ctx: BotContext
) -> None:
    await dispatcher.feed_update(bot, admin_press(MAINTENANCE, sender_id=STRANGER_ID))

    assert await ctx.site_banner.current() is SiteBanner.NONE


async def test_remnawave_alert_offers_banner_and_button_sets_it(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    ctx: BotContext,
    alert_sender: FakeAlertSender,
) -> None:
    await ctx.alerter.raise_alert(REMNAWAVE_UNAVAILABLE_ALERT, "Remnawave не отвечает")

    [alert] = alert_sender.sent
    button = banner_alert_button(content.admin_texts)
    assert alert.button == button

    await dispatcher.feed_update(bot, admin_press(button.callback_data))

    assert await ctx.site_banner.current() is SiteBanner.PROXY_ISSUES
    # тревогу не переписываем — экран приходит ответом на неё
    assert telegram.sent(EditMessageText) == []
    [screen] = telegram.sent(SendMessage)
    assert state_text(content, SiteBanner.PROXY_ISSUES) in (screen.text or "")


async def test_other_alerts_have_no_button(ctx: BotContext, alert_sender: FakeAlertSender) -> None:
    await ctx.alerter.raise_alert("something_else", "Другая тревога")

    [alert] = alert_sender.sent
    assert alert.button is None
