"""`/proof 123` в группе администраторов: файл с доказательством оказания услуги."""

from datetime import timedelta
from decimal import Decimal

import pytest
from aiogram import Bot, Dispatcher
from aiogram.methods import SendDocument, SendMessage
from aiogram.types import BufferedInputFile, Chat, Update
from bot_updates import ADMIN_CHAT_ID, ADMIN_ID, NOW, message, user
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.bot.admin_proof import create_admin_proof_router
from tsproxy.bot.context import BotContext
from tsproxy.bot.dispatcher import create_dispatcher
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.bot.throttling import RateLimiter
from tsproxy.content.loader import Content
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Event, Order
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.services.events import EventKind
from tsproxy.services.fakes import FakeClock
from tsproxy.services.service_proof import ServiceProofService

ADMIN_GROUP = Chat(id=ADMIN_CHAT_ID, type="supergroup", title="Админы", is_forum=True)
STRANGER_ID = 333


@pytest.fixture
def proof_dispatcher(
    ctx: BotContext,
    limiter: RateLimiter,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    clock: FakeClock,
) -> Dispatcher:
    proofs = ServiceProofService(session_factory, remnawave, clock)
    return create_dispatcher(
        ctx, limiter, extra_routers=[create_admin_proof_router(ctx.access, proofs)]
    )


@pytest.fixture
async def order_id(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        client = Client(telegram_id=111, name="Иван & Ко", username="ivan", first_seen_at=NOW)
        session.add(client)
        await session.flush()
        order = Order(
            client_id=client.id,
            tariff_id="m1",
            price=Decimal("150.00"),
            days=30,
            status=OrderStatus.ACCESS_GRANTED,
            created_at=NOW,
            paid_at=NOW + timedelta(minutes=1),
            paid_amount=Decimal("150.00"),
            payment_provider="fake",
            payment_id="pay-1",
        )
        session.add(order)
        await session.flush()
        session.add(
            Event(
                kind=EventKind.ACCESS_GRANTED.value,
                client_id=client.id,
                order_id=order.id,
                occurred_at=NOW + timedelta(minutes=2),
                details={"expire_at": "2026-11-24T12:02:00+00:00"},
            )
        )
        await session.commit()
        return order.id


def proof_command(text: str, *, sender_id: int = ADMIN_ID) -> Update:
    return message(user(sender_id, first_name="Админ"), text, chat=ADMIN_GROUP)


async def test_proof_is_sent_as_text_file(
    proof_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, order_id: int
) -> None:
    await proof_dispatcher.feed_update(bot, proof_command(f"/proof {order_id}"))

    [sent] = telegram.sent(SendDocument)
    assert sent.chat_id == ADMIN_CHAT_ID
    document = sent.document
    assert isinstance(document, BufferedInputFile)
    assert document.filename == f"proof-order-{order_id}.txt"
    text = document.data.decode("utf-8")
    assert f"заказ #{order_id}" in text
    assert "имя: Иван & Ко" in text  # обычный текст, без HTML-экранирования
    assert "номер платежа: pay-1" in text
    assert "доступ выдан (подписка продлена)" in text
    assert "Прокси-аккаунта в панели нет" in text


async def test_unknown_order_and_bad_usage(
    proof_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, content: Content
) -> None:
    await proof_dispatcher.feed_update(bot, proof_command("/proof 404"))
    await proof_dispatcher.feed_update(bot, proof_command("/proof"))

    not_found, usage = telegram.sent(SendMessage)
    assert not_found.text == content.admin_texts.get("admin.proof.not_found", order_id=404)
    assert usage.text == content.admin_texts.get("admin.proof.usage")


async def test_not_admin_gets_nothing(
    proof_dispatcher: Dispatcher, bot: Bot, telegram: FakeTelegramSession, order_id: int
) -> None:
    update = proof_command(f"/proof {order_id}", sender_id=STRANGER_ID)
    await proof_dispatcher.feed_update(bot, update)
    assert telegram.sent(SendDocument) == []


def test_every_event_kind_has_a_proof_text(content: Content) -> None:
    for kind in EventKind:
        assert content.admin_texts.get(f"admin.proof.kinds.{kind.value}")
