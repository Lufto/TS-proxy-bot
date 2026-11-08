"""Адрес уведомлений платёжки (задача 14): коды ответа и что за ними стоит.

Лежит рядом с тестами проверки оплат: те же подделки (`payment_helpers`) и фикстуры.
"""

from collections.abc import AsyncIterator

import httpx
import pytest
from payment_helpers import PaymentSetup, build_payment_setup, flaky_commits
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client
from tsproxy.integrations.fake_payments import CANCELED_EVENT, notification_body
from tsproxy.integrations.payments_types import PaymentUnavailable
from tsproxy.services.events import EventKind
from tsproxy.services.fakes import FakeClock
from tsproxy.services.masking import Masker
from tsproxy.web.app import create_web_app
from tsproxy.web.payments import (
    MAX_BODY_BYTES,
    PAYMENT_NOTIFICATION_PATH,
    PaymentNotificationWebhook,
)


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def s(
    session_factory: async_sessionmaker[AsyncSession], clock: FakeClock, mask: Masker
) -> PaymentSetup:
    return build_payment_setup(session_factory, clock, mask)


async def http_for(s: PaymentSetup) -> httpx.AsyncClient:
    app = create_web_app(None, payments=PaymentNotificationWebhook(s.payments, s.checks))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://bot.test")


@pytest.fixture
async def http(s: PaymentSetup) -> AsyncIterator[httpx.AsyncClient]:
    async with await http_for(s) as http:
        yield http


async def notify(http: httpx.AsyncClient, body: bytes) -> int:
    response = await http.post(PAYMENT_NOTIFICATION_PATH, content=body)
    return response.status_code


async def test_paid_notification_credits_order(
    s: PaymentSetup, http: httpx.AsyncClient, client: Client
) -> None:
    order_id = await s.order(client, paid=True)

    assert await notify(http, notification_body(await s.payment_id(order_id))) == 200
    assert await notify(http, notification_body(await s.payment_id(order_id))) == 200

    assert (await s.load(order_id)).status is OrderStatus.ACCESS_GRANTED
    assert len(s.messages.granted) == 1


async def test_canceled_notification_is_rechecked(
    s: PaymentSetup, http: httpx.AsyncClient, client: Client
) -> None:
    order_id = await s.order(client)
    body = notification_body(await s.payment_id(order_id), CANCELED_EVENT)

    assert await notify(http, body) == 200

    assert (await s.load(order_id)).status is OrderStatus.CREATED  # у платёжки не отменён


async def test_unknown_payment_is_accepted_and_journaled(
    s: PaymentSetup, http: httpx.AsyncClient
) -> None:
    assert await notify(http, notification_body("fake_invented")) == 200

    assert s.payments.asked == []
    [event] = await s.events(EventKind.FAKE_PAYMENT_NOTIFICATION)
    assert event.details is not None
    assert event.details["source"] == "127.0.0.1"


async def test_garbage_is_rejected_and_journaled(s: PaymentSetup, http: httpx.AsyncClient) -> None:
    assert await notify(http, b"<xml>hello</xml>") == 400

    [event] = await s.events(EventKind.FAKE_PAYMENT_NOTIFICATION)
    assert event.details is not None
    assert event.details["reason"] == "unparsed"
    assert len(s.alerts.sent) == 1


async def test_large_body_is_rejected_unread(s: PaymentSetup, http: httpx.AsyncClient) -> None:
    assert await notify(http, b"x" * (MAX_BODY_BYTES + 1)) == 413

    assert await s.events(EventKind.FAKE_PAYMENT_NOTIFICATION) == []


async def test_provider_down_asks_for_retry(
    s: PaymentSetup, http: httpx.AsyncClient, client: Client
) -> None:
    order_id = await s.order(client, paid=True)
    s.payments.fail_next(PaymentUnavailable("таймаут"))

    assert await notify(http, notification_body(await s.payment_id(order_id))) == 503


async def test_database_failure_asks_for_retry(
    session_factory: async_sessionmaker[AsyncSession],
    clock: FakeClock,
    mask: Masker,
    client: Client,
) -> None:
    s = build_payment_setup(
        session_factory, clock, mask, crediting_sessions=flaky_commits(session_factory, 100)
    )
    order_id = await s.order(client, paid=True)

    async with await http_for(s) as http:
        assert await notify(http, notification_body(await s.payment_id(order_id))) == 500

    assert (await s.load(order_id)).status is OrderStatus.CREATED


async def test_no_endpoint_without_payments() -> None:
    """`PAYMENTS_MODE=off`: уведомления не принимаем — перепроверить их не у кого."""
    app = create_web_app(None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bot.test") as http:
        assert await notify(http, notification_body("fake_1")) == 404
