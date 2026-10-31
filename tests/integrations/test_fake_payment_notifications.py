"""Поддельная платёжка, задача 14: уведомления, список платежей для сверки, возвраты в статусе."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tsproxy.domain.constants import PAYMENT_LINK_LIFETIME
from tsproxy.integrations.fake_payments import (
    CANCELED_EVENT,
    FakePaymentProvider,
    notification_body,
)
from tsproxy.integrations.payments_types import (
    InvalidNotification,
    PaymentNotice,
    PaymentRequest,
    PaymentStatus,
    PaymentUnavailable,
)
from tsproxy.services.fakes import FakeClock

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
NO_HEADERS: dict[str, str] = {}


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def provider(clock: FakeClock) -> FakePaymentProvider:
    return FakePaymentProvider(clock)


def request(order_id: int) -> PaymentRequest:
    return PaymentRequest(order_id, Decimal("150"), "TS Proxy, 1 месяц", f"key-{order_id}")


def test_notification_gives_payment_id_and_hints(provider: FakePaymentProvider) -> None:
    first = provider.parse_notification(notification_body("fake_1"), NO_HEADERS)
    assert first == PaymentNotice("fake_1", PaymentStatus.SUCCEEDED)
    body = notification_body("fake_2", CANCELED_EVENT)
    assert provider.parse_notification(body, NO_HEADERS) == PaymentNotice(
        "fake_2", PaymentStatus.CANCELED
    )
    refund = notification_body("fake_3", "payment.refunded", refund_id="r1")
    assert provider.parse_notification(refund, NO_HEADERS) == PaymentNotice(
        "fake_3", PaymentStatus.REFUNDED, "r1"
    )
    unknown = notification_body("fake_4", "refund.succeeded")
    assert provider.parse_notification(unknown, NO_HEADERS) == PaymentNotice("fake_4")


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"not json",
        b"[]",
        b'{"event": "payment.succeeded"}',
        b'{"event": "payment.succeeded", "object": {"id": ""}}',
        b'{"event": "payment.succeeded", "object": {"id": 5}}',
        notification_body("x" * 129),
    ],
)
def test_broken_notification_is_rejected(provider: FakePaymentProvider, body: bytes) -> None:
    with pytest.raises(InvalidNotification):
        provider.parse_notification(body, NO_HEADERS)


async def test_list_payments_by_creation_time_with_current_status(
    provider: FakePaymentProvider, clock: FakeClock
) -> None:
    first = await provider.create_payment(request(1))
    clock.advance(timedelta(minutes=10))
    second = await provider.create_payment(request(2))
    clock.advance(timedelta(minutes=10))
    await provider.create_payment(request(3))
    provider.ledger.pay(second.payment_id, clock())
    provider.ledger.refund(second.payment_id, Decimal("50"), "refund-1")
    clock.advance(PAYMENT_LINK_LIFETIME)

    listed = await provider.list_payments(NOW, NOW + timedelta(minutes=20))

    assert [(p.payment_id, p.status, p.refunded) for p in listed] == [
        (first.payment_id, PaymentStatus.CANCELED, Decimal(0)),
        (second.payment_id, PaymentStatus.SUCCEEDED, Decimal("50")),
    ]


async def test_list_payments_can_fail(provider: FakePaymentProvider) -> None:
    provider.fail_next(PaymentUnavailable("таймаут"))

    with pytest.raises(PaymentUnavailable):
        await provider.list_payments(NOW, NOW + timedelta(days=1))
