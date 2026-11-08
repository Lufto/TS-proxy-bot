"""Platega целиком (задача 15): колбэк → проверка ключей → перепроверка `GET` → зачёт;
спор через банк по тому же платежу. Сеть подделана `httpx.MockTransport`."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from payment_helpers import PRICE, PaymentSetup, build_payment_setup
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Order
from tsproxy.integrations.platega import (
    PLATEGA_PROVIDER,
    PlategaPaymentProvider,
    create_platega_http_client,
)
from tsproxy.integrations.remnawave_types import AccountStatus
from tsproxy.services.events import EventLog
from tsproxy.services.fakes import FakeClock
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.masking import Masker
from tsproxy.services.payment_checks import PaymentChecks
from tsproxy.services.payment_status import PaymentStatusChanges
from tsproxy.web.app import create_web_app
from tsproxy.web.payments import PAYMENT_NOTIFICATION_PATH, PaymentNotificationWebhook

MERCHANT = "merchant-for-tests"
SECRET = "secret-for-tests"
PAYMENT = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
HEADERS = {"X-MerchantId": MERCHANT, "X-Secret": SECRET, "User-Agent": "Platega Payment"}


class FakePlatega:
    """Platega по HTTP: отвечает статусом платежа, который задал тест."""

    def __init__(self) -> None:
        self.status = "CONFIRMED"
        self.order_id = 0
        self.asked = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/transaction/{PAYMENT}"
        assert request.headers["X-Secret"] == SECRET
        self.asked += 1
        body: dict[str, Any] = {
            "id": PAYMENT,
            "status": self.status,
            "paymentDetails": {"amount": int(PRICE), "currency": "RUB"},
            "payload": str(self.order_id),
        }
        return httpx.Response(200, json=body)


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def s(
    session_factory: async_sessionmaker[AsyncSession], clock: FakeClock, mask: Masker
) -> PaymentSetup:
    return build_payment_setup(session_factory, clock, mask)


@pytest.fixture
def platega() -> FakePlatega:
    return FakePlatega()


@pytest.fixture
async def http(
    s: PaymentSetup, platega: FakePlatega, mask: Masker
) -> AsyncIterator[httpx.AsyncClient]:
    provider = PlategaPaymentProvider(
        create_platega_http_client(transport=httpx.MockTransport(platega.handle)),
        MERCHANT,
        SECRET,
        "https://t.me/ts_proxy_test_bot",
        s.clock,
        test=True,
    )
    events = EventLog(s.clock, mask)
    checks = PaymentChecks(
        s.session_factory,
        provider,
        s.crediting,
        events,
        s.alerter,
        s.admin_texts,
        s.clock,
        test_payments=True,
        status_changes=PaymentStatusChanges(
            s.session_factory,
            s.remnawave,
            s.reviews,
            events,
            s.alerter,
            s.admin_texts,
            s.messages,
            KeyedLocks(),
            s.clock,
        ),
    )
    app = create_web_app(None, payments=PaymentNotificationWebhook(provider, checks))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://bot.test") as client:
        yield client
    await provider.aclose()


async def platega_order(s: PaymentSetup, client: Client) -> int:
    async with s.session_factory() as session:
        order = Order(
            client_id=client.id,
            tariff_id="m1",
            price=PRICE,
            days=30,
            status=OrderStatus.CREATED,
            created_at=s.clock(),
            payment_provider=PLATEGA_PROVIDER,
            payment_id=PAYMENT,
        )
        session.add(order)
        await session.commit()
        return order.id


async def callback(http: httpx.AsyncClient, status: str, headers: dict[str, str]) -> int:
    body = {"id": PAYMENT, "amount": 150, "currency": "RUB", "status": status, "paymentMethod": 2}
    response = await http.post(PAYMENT_NOTIFICATION_PATH, content=json.dumps(body), headers=headers)
    return response.status_code


async def test_confirmed_callback_credits_once_then_chargeback_disables(
    s: PaymentSetup, http: httpx.AsyncClient, platega: FakePlatega, client: Client
) -> None:
    platega.order_id = await platega_order(s, client)

    assert await callback(http, "CONFIRMED", HEADERS) == 200
    assert await callback(http, "CONFIRMED", HEADERS) == 200  # повтор колбэка

    assert (await s.load(platega.order_id)).status is OrderStatus.ACCESS_GRANTED
    assert len(s.messages.granted) == 1
    assert platega.asked == 1  # повтор по засчитанному заказу — без запроса

    platega.status = "CHARGEBACKED"
    assert await callback(http, "CHARGEBACKED", HEADERS) == 200

    account = await s.remnawave.get_account(client.telegram_id)
    assert account.status is AccountStatus.DISABLED
    # других оплаченных дней нет — после снятия дней этой оплаты доступ выключен
    assert s.messages.disputed == [(client.telegram_id, None, True)]


async def test_callback_with_wrong_secret_is_rejected_unasked(
    s: PaymentSetup, http: httpx.AsyncClient, platega: FakePlatega, client: Client
) -> None:
    platega.order_id = await platega_order(s, client)

    status = await callback(http, "CONFIRMED", {**HEADERS, "X-Secret": "guess"})

    assert status == 400
    assert platega.asked == 0
    assert (await s.load(platega.order_id)).status is OrderStatus.CREATED


async def test_payment_for_other_order_is_not_credited(
    s: PaymentSetup, http: httpx.AsyncClient, platega: FakePlatega, client: Client
) -> None:
    """`payload` платежа — не номер этого заказа: не засчитываем, тревога."""
    order_id = await platega_order(s, client)
    platega.order_id = order_id + 100

    assert await callback(http, "CONFIRMED", HEADERS) == 200

    assert (await s.load(order_id)).status is OrderStatus.CREATED
    assert len(s.alerts.sent) == 1
