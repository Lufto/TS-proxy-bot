"""Сообщения о льготном периоде (задача 11): текст, кнопка «Продлить», заблокировавший бота."""

from datetime import UTC, datetime

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from aiogram.types import InlineKeyboardMarkup
from bot_updates import CLIENT_ID

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.context import BotContext
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.grace import TelegramGraceMessenger, grace_screen
from tsproxy.content.loader import REQUIRED_TEXTS, Content, Texts
from tsproxy.domain.grace import GraceStage

ACCESS_UNTIL = datetime(2026, 11, 14, 15, 0, tzinfo=UTC)  # 18:00 по Москве


@pytest.mark.parametrize("stage", list(GraceStage))
def test_every_notice_has_renew_button(content: Content, stage: GraceStage) -> None:
    screen = grace_screen(content.texts, stage, ACCESS_UNTIL)

    [[button]] = screen.markup.inline_keyboard
    assert button.text == content.texts.get("buttons.renew")
    assert button.callback_data == MenuCallback(screen=ScreenId.TARIFFS).pack()


@pytest.mark.parametrize("stage", [GraceStage.STARTED, GraceStage.REMINDER, GraceStage.LAST_DAY])
def test_notice_tells_when_access_ends_in_moscow_time(content: Content, stage: GraceStage) -> None:
    screen = grace_screen(content.texts, stage, ACCESS_UNTIL)
    assert "14.11.2026, 18:00 МСК" in screen.text


@pytest.mark.parametrize("stage", list(GraceStage))
def test_notices_use_only_required_texts(content: Content, stage: GraceStage) -> None:
    # иначе бот упал бы у клиента, а не при запуске
    texts = Texts({key: content.texts._items[key] for key in REQUIRED_TEXTS}, "required")
    grace_screen(texts, stage, ACCESS_UNTIL)


async def test_messenger_sends_to_client(ctx: BotContext, telegram: FakeTelegramSession) -> None:
    messenger = TelegramGraceMessenger(ctx.delivery, ctx.texts)

    delivered = await messenger.send_grace_notice(CLIENT_ID, GraceStage.LAST_DAY, ACCESS_UNTIL)

    assert delivered is True
    [sent] = telegram.sent(SendMessage)
    assert sent.chat_id == CLIENT_ID
    assert sent.text == grace_screen(ctx.texts, GraceStage.LAST_DAY, ACCESS_UNTIL).text
    assert isinstance(sent.reply_markup, InlineKeyboardMarkup)


async def test_messenger_reports_blocked_client(
    ctx: BotContext, telegram: FakeTelegramSession
) -> None:
    telegram.fail_next(
        SendMessage, TelegramForbiddenError, "Forbidden: bot was blocked by the user"
    )
    messenger = TelegramGraceMessenger(ctx.delivery, ctx.texts)

    assert await messenger.send_grace_notice(CLIENT_ID, GraceStage.ENDED, ACCESS_UNTIL) is False
