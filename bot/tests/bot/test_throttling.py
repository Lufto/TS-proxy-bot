"""Ограничение частоты: флуд кнопками и сообщениями не доходит до обработчиков."""

from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery, SendMessage
from bot_updates import ADMIN_ID, CLIENT_ID, OTHER_CLIENT_ID, callback, message, user
from conftest import ManualMonotonic

from tsproxy.bot.callbacks import MenuCallback, ScreenId
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.throttling import (
    RATE_LIMIT_BURST,
    RATE_LIMIT_PER_MINUTE,
    SECONDS_PER_MINUTE,
    RateDecision,
    RateLimiter,
)
from tsproxy.content.loader import Content

ABOUT = MenuCallback(screen=ScreenId.ABOUT).pack()
SECONDS_PER_TOKEN = SECONDS_PER_MINUTE / RATE_LIMIT_PER_MINUTE


def test_burst_then_deny_then_refill(monotonic: ManualMonotonic) -> None:
    limiter = RateLimiter(clock=monotonic)
    decisions = [limiter.check(CLIENT_ID) for _ in range(RATE_LIMIT_BURST + 2)]

    assert decisions == [RateDecision.ALLOW] * RATE_LIMIT_BURST + [
        RateDecision.DENY_FIRST,
        RateDecision.DENY,
    ]
    assert limiter.check(OTHER_CLIENT_ID) is RateDecision.ALLOW  # у каждого своё ведро
    monotonic.now += SECONDS_PER_TOKEN
    assert limiter.check(CLIENT_ID) is RateDecision.ALLOW
    assert limiter.check(CLIENT_ID) is RateDecision.DENY_FIRST  # снова предупредим один раз


def test_sustained_rate_is_limited_per_minute(monotonic: ManualMonotonic) -> None:
    limiter = RateLimiter(clock=monotonic)
    allowed = 0
    for _ in range(SECONDS_PER_MINUTE * 10):  # 10 нажатий в секунду в течение минуты
        allowed += limiter.check(CLIENT_ID) is RateDecision.ALLOW
        monotonic.now += 0.1
    assert allowed <= RATE_LIMIT_BURST + RATE_LIMIT_PER_MINUTE


def test_idle_clients_are_forgotten(monotonic: ManualMonotonic) -> None:
    limiter = RateLimiter(clock=monotonic, max_tracked=2)
    limiter.check(1)
    limiter.check(2)
    monotonic.now += SECONDS_PER_MINUTE
    limiter.check(3)
    assert len(limiter._buckets) == 1


async def test_button_flood_is_not_processed(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    flood = RATE_LIMIT_BURST + 5
    for _ in range(flood):
        await dispatcher.feed_update(bot, callback(user(CLIENT_ID), ABOUT))

    assert len(telegram.sent(SendMessage)) == RATE_LIMIT_BURST
    throttled = [
        a
        for a in telegram.sent(AnswerCallbackQuery)
        if a.text == content.texts.get("throttle.callback")
    ]
    assert len(throttled) == flood - RATE_LIMIT_BURST  # каждой лишней кнопке — ответ


async def test_command_flood_gets_one_warning(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    # обычные сообщения — обращения в поддержку, их предел проверяет tests/bot/test_support_bot.py
    for _ in range(RATE_LIMIT_BURST + 5):
        await dispatcher.feed_update(bot, message(user(CLIENT_ID), "/start"))

    texts = [m.text for m in telegram.sent(SendMessage)]
    assert texts.count(content.texts.get("menu.title")) == RATE_LIMIT_BURST
    assert texts.count(content.texts.get("throttle.message")) == 1


async def test_admins_are_not_limited(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession
) -> None:
    for _ in range(RATE_LIMIT_BURST + 5):
        await dispatcher.feed_update(bot, callback(user(ADMIN_ID), ABOUT))

    assert len(telegram.sent(SendMessage)) == RATE_LIMIT_BURST + 5
