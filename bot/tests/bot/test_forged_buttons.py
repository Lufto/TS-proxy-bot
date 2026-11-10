"""Подделанные данные кнопок клиента (аудит перед оплатой, задача 03).

Модифицированное приложение Telegram пришлёт в кнопке что угодно. Ни одна такая кнопка
не создаёт заказ, прокси-аккаунт или пробный, не трогает чужое и не роняет бота.
"""

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import AnswerCallbackQuery
from bot_updates import CLIENT_ID, OTHER_CLIENT_ID, callback, user
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.db.models import Client, Order
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import AccountNotFound
from tsproxy.services.fakes import FakeAlertSender

# Данные, которые не подходят ни одной кнопке: обработчик не найдётся
MALFORMED = [
    "device:zzzz",  # не хеш устройства
    "device:" + "0" * 200,
    "paycheck:abc",  # номер заказа не число
    "paycheck:-1:extra",
    "menu:admin",  # такого экрана нет
    "howto:../../etc",
    "relink:1",  # у «перевыпустить» данных нет
    "x" * 64,
    "",
]

# Подходят по форме, но указывают на то, чего нет
UNKNOWN_TARGET = [
    "buy:no_such_tariff",
    "stars:no_such_tariff",
    "tariff:no_such_tariff",
    "paycheck:999999",
]


async def count_orders(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        return await session.scalar(select(func.count()).select_from(Order)) or 0


@pytest.mark.parametrize("data", MALFORMED)
async def test_malformed_button_only_asks_to_restart(
    data: str,
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    remnawave: FakeRemnawave,
    alert_sender: FakeAlertSender,
    session_factory: async_sessionmaker[AsyncSession],
    content: Content,
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data))

    [answer] = telegram.sent(AnswerCallbackQuery)
    assert answer.text == content.texts.get("errors.stale_button")
    assert await count_orders(session_factory) == 0
    with pytest.raises(AccountNotFound):
        await remnawave.get_account(CLIENT_ID)
    assert alert_sender.sent == []


@pytest.mark.parametrize("data", UNKNOWN_TARGET)
async def test_button_with_unknown_target_creates_nothing(
    data: str,
    dispatcher: Dispatcher,
    bot: Bot,
    remnawave: FakeRemnawave,
    alert_sender: FakeAlertSender,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), data))

    assert await count_orders(session_factory) == 0
    with pytest.raises(AccountNotFound):
        await remnawave.get_account(CLIENT_ID)
    assert alert_sender.sent == []


async def test_real_tariff_button_does_create_order(
    dispatcher: Dispatcher,
    bot: Bot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Проверка теста выше: настоящий тариф заказ создаёт — значит, оплата в тестах включена."""
    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), "buy:m1"))

    assert await count_orders(session_factory) == 1


async def test_check_button_with_other_clients_order_number_reveals_nothing(
    dispatcher: Dispatcher,
    bot: Bot,
    telegram: FakeTelegramSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Номер чужого заказа в «🔄 Проверить оплату»: ни статуса, ни ссылки на оплату."""
    async with session_factory() as session:
        victim = Client(telegram_id=OTHER_CLIENT_ID)
        session.add(victim)
        await session.flush()
        order = Order(
            client_id=victim.id,
            tariff_id="m1",
            price=150,
            days=30,
            payment_provider="fake",
            payment_id="victim-payment",
            payment_url="https://pay.example/victim",
        )
        session.add(order)
        await session.commit()
        order_id = order.id

    await dispatcher.feed_update(bot, callback(user(CLIENT_ID), f"paycheck:{order_id}"))

    for request in telegram.requests:
        dumped = request.model_dump_json(fallback=repr)
        assert "victim-payment" not in dumped
        assert "https://pay.example/victim" not in dumped
