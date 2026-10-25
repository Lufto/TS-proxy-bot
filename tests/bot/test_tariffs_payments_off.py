"""«💳 Тарифы», `/buy` и старые кнопки тарифов, когда оплата выключена (задача 29)."""

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import EditMessageText, SendMessage
from aiogram.types import InlineKeyboardMarkup
from bot_updates import ADMIN_ID, CLIENT_ID, NOW, callback, message, user
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.callbacks import MenuCallback, ScreenId, TariffCallback
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.tariffs import TariffScreens
from tsproxy.content.loader import REQUIRED_TEXTS, Content, Texts
from tsproxy.db.models import Client, Event, Order
from tsproxy.services.orders import TariffOffer

TARIFFS = MenuCallback(screen=ScreenId.TARIFFS).pack()
TRIAL = MenuCallback(screen=ScreenId.TRIAL).pack()
MAIN = MenuCallback(screen=ScreenId.MAIN).pack()


@pytest.fixture
def payments() -> None:
    """Оплата выключена: платёжки нет (`PAYMENTS_MODE=off`)."""
    return None


def button_data(markup: InlineKeyboardMarkup | None) -> list[str | None]:
    assert markup is not None
    return [button.callback_data for row in markup.inline_keyboard for button in row]


async def add_client_with_trial(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(Client(telegram_id=CLIENT_ID, first_seen_at=NOW, trial_granted_at=NOW))
        await session.commit()


async def test_tariffs_listed_without_buy_buttons_and_trial_offered(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), TARIFFS))

    edited = telegram.sent(EditMessageText)[-1]
    assert edited.text.startswith(content.texts.get("tariffs.off_title"))
    assert "1 месяц — 150 ₽" in edited.text
    assert "3 месяца — 405 ₽ · выгода 10%" in edited.text
    assert "Тест — 1 день" not in edited.text  # скрытый тариф — только администраторам
    assert edited.text.endswith(content.texts.get("tariffs.off_trial"))
    assert button_data(edited.reply_markup) == [TRIAL, MAIN]  # type: ignore[arg-type]


async def test_no_trial_button_when_trial_already_used(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await add_client_with_trial(session_factory)

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), TARIFFS))

    edited = telegram.sent(EditMessageText)[-1]
    assert edited.text.endswith(content.texts.get("tariffs.off_later"))
    assert button_data(edited.reply_markup) == [MAIN]  # type: ignore[arg-type]


async def test_buy_command_shows_payments_off_screen(
    dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await dispatcher.feed_update(bot, message(user(ADMIN_ID), "/buy"))

    [sent] = telegram.sent(SendMessage)
    assert sent.text.startswith(content.texts.get("tariffs.off_title"))
    assert "Тест — 1 день" in sent.text  # администратор видит и скрытый тариф
    assert not any(
        data and data.startswith("buy:")
        for data in button_data(sent.reply_markup)  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("tariff_id", ["m1", "test15", "gone"])
async def test_old_tariff_button_creates_no_order(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    content: Content,
    session_factory: async_sessionmaker[AsyncSession],
    tariff_id: str,
) -> None:
    """«Готово, когда» задачи 29: «Купить» не создаёт заказ и платёж."""
    data = TariffCallback(tariff_id=tariff_id).pack()

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data))

    edited = telegram.sent(EditMessageText)[-1]
    assert edited.text.startswith(content.texts.get("tariffs.off_title"))
    async with session_factory() as session:
        assert list(await session.scalars(select(Order))) == []
        assert list(await session.scalars(select(Event))) == []


def test_off_screen_uses_only_required_texts(content: Content) -> None:
    texts = Texts({key: content.texts._items[key] for key in REQUIRED_TEXTS}, "required")
    screens = TariffScreens(texts)
    offers = tuple(TariffOffer(t, 10) for t in content.tariffs)
    screens.payments_off(offers, trial_available=True)
    screens.payments_off(offers, trial_available=False)
