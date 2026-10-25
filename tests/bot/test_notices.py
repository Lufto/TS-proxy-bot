"""Уведомления клиенту (задача 10): тексты, кнопки, отправка и пометка «заблокировал бота»."""

from datetime import timedelta
from pathlib import Path

import pytest
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from bot_updates import CLIENT_ID, NOW
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from test_app import make_settings

from tsproxy.app import build_application, close_application
from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.context import BotContext
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.notices import NoticeScreens, TelegramNotifier
from tsproxy.content.loader import REQUIRED_TEXTS, Content, Texts
from tsproxy.db.models import Client
from tsproxy.domain.reminders import AccessPeriod, Reminder, ReminderKind, TrialTrafficNotice
from tsproxy.services.reminders import (
    ClientNotice,
    ExpiryReminder,
    ReminderService,
    TrialExtended,
    TrialFarewell,
    TrialTraffic,
)
from tsproxy.services.term_guard import TermLockedHandlers

GB = 1024**3
EXPIRE_AT = NOW + timedelta(days=1)  # 13:00 МСК следующего дня

ALL_NOTICES: list[ClientNotice] = [
    *(
        ExpiryReminder(Reminder(kind, period, EXPIRE_AT))
        for period in AccessPeriod
        for kind in ReminderKind
    ),
    TrialExtended(EXPIRE_AT),
    TrialFarewell(),
    TrialTraffic(TrialTrafficNotice.WARNING, 6 * GB, 7 * GB),
    TrialTraffic(TrialTrafficNotice.USED_UP, 7 * GB, 7 * GB),
]


def screens_of(content: Content) -> NoticeScreens:
    return NoticeScreens(content.texts)


def callbacks(notice: ClientNotice, content: Content) -> list[ScreenId]:
    markup = screens_of(content).notice(notice).markup
    return [
        MenuCallback.unpack(button.callback_data).screen
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data is not None
    ]


@pytest.mark.parametrize("notice", ALL_NOTICES)
def test_every_notice_uses_only_required_texts(content: Content, notice: ClientNotice) -> None:
    # текст не из REQUIRED_TEXTS уронил бы бота у клиента, а не при запуске
    texts = Texts({key: content.texts._items[key] for key in REQUIRED_TEXTS}, "required")
    assert NoticeScreens(texts).notice(notice).text


def test_reminder_shows_moscow_time_and_renew_button(content: Content) -> None:
    notice = ExpiryReminder(Reminder(ReminderKind.DAY_BEFORE, AccessPeriod.PAID, EXPIRE_AT))
    screen = screens_of(content).notice(notice)
    assert "25.10.2026, 15:00 МСК" in screen.text
    assert screen.markup.inline_keyboard[0][0].text == content.texts.get("buttons.renew")
    assert callbacks(notice, content) == [ScreenId.TARIFFS, ScreenId.MAIN]


def test_trial_and_paid_reminders_differ(content: Content) -> None:
    screens = screens_of(content)
    trial = screens.notice(
        ExpiryReminder(Reminder(ReminderKind.HOUR_BEFORE, AccessPeriod.TRIAL, EXPIRE_AT))
    )
    paid = screens.notice(
        ExpiryReminder(Reminder(ReminderKind.HOUR_BEFORE, AccessPeriod.PAID, EXPIRE_AT))
    )
    assert trial.text != paid.text
    assert trial.markup.inline_keyboard[0][0].text == content.texts.get("buttons.tariffs")


def test_trial_extended_says_three_days_and_offers_connect(content: Content) -> None:
    notice = TrialExtended(EXPIRE_AT)
    screen = screens_of(content).notice(notice)
    assert "3 дня" in screen.text
    assert "25.10.2026, 15:00 МСК" in screen.text
    assert callbacks(notice, content) == [ScreenId.CONNECT, ScreenId.INSTRUCTIONS, ScreenId.MAIN]


@pytest.mark.parametrize(
    "notice",
    [
        TrialFarewell(),
        TrialTraffic(TrialTrafficNotice.WARNING, 6 * GB, 7 * GB),
        TrialTraffic(TrialTrafficNotice.USED_UP, 7 * GB, 7 * GB),
    ],
)
def test_trial_end_notices_offer_tariffs(content: Content, notice: ClientNotice) -> None:
    assert callbacks(notice, content) == [ScreenId.TARIFFS, ScreenId.MAIN]


def test_traffic_warning_shows_gigabytes(content: Content) -> None:
    notice = TrialTraffic(TrialTrafficNotice.WARNING, 5 * GB + GB // 2, 7 * GB)
    assert "5,5 из 7 ГБ" in screens_of(content).notice(notice).text


async def test_notifier_sends_new_message(
    ctx: BotContext, content: Content, telegram: FakeTelegramSession
) -> None:
    notifier = TelegramNotifier(screens_of(content), ctx.delivery)

    await notifier.notify(CLIENT_ID, TrialFarewell())

    [sent] = telegram.sent(SendMessage)
    assert sent.chat_id == CLIENT_ID
    assert sent.text == content.texts.get("notices.trial_farewell")


async def test_notifier_marks_client_who_blocked_bot(
    ctx: BotContext,
    content: Content,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(Client(telegram_id=CLIENT_ID, name="Иван", first_seen_at=NOW))
        await session.commit()
    telegram.fail_next(
        SendMessage, TelegramForbiddenError, "Forbidden: bot was blocked by the user"
    )
    notifier = TelegramNotifier(screens_of(content), ctx.delivery)

    await notifier.notify(CLIENT_ID, TrialFarewell())

    async with session_factory() as session:
        blocked_at = await session.scalar(
            select(Client.bot_blocked_at).where(Client.telegram_id == CLIENT_ID)
        )
    assert blocked_at is not None


async def test_application_checks_accounts_with_reminders(tmp_path: Path, content: Content) -> None:
    app = build_application(
        make_settings(tmp_path), content, telegram_session=FakeTelegramSession()
    )
    await close_application(app)
    [job] = [j for j in app.jobs._jobs if j.name == "remnawave_reconcile"]
    reconciliation = job.run.__self__  # type: ignore[attr-defined]
    [guarded] = reconciliation._accounts._handlers
    assert isinstance(guarded, TermLockedHandlers)
    assert any(isinstance(handler, ReminderService) for handler in guarded.handlers)
