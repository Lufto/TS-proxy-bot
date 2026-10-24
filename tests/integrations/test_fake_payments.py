"""Поддельная платёжка: та же логика, что ждём от настоящей (задача 12)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tsproxy.domain.constants import PAYMENT_LINK_LIFETIME
from tsproxy.integrations.fake_payments import (
    FAKE_PROVIDER_NAME,
    FakePaymentLedger,
    FakePaymentProvider,
)
from tsproxy.integrations.payments_types import (
    PaymentNotFound,
    PaymentProvider,
    PaymentRejected,
    PaymentRequest,
    PaymentStatus,
    PaymentUnavailable,
    RefundOutcome,
)
from tsproxy.services.fakes import FakeClock

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def request(order_id: int = 1, amount: str = "405", key: str = "key-1") -> PaymentRequest:
    return PaymentRequest(
        order_id=order_id,
        amount=Decimal(amount),
        description="TS Proxy, 3 месяца",
        idempotency_key=key,
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def provider(clock: FakeClock) -> FakePaymentProvider:
    return FakePaymentProvider(clock)


def test_fake_provider_follows_the_interface(provider: FakePaymentProvider) -> None:
    checked: PaymentProvider = provider
    assert checked.name == FAKE_PROVIDER_NAME


async def test_created_payment_is_pending_with_link(provider: FakePaymentProvider) -> None:
    created = await provider.create_payment(request())

    assert created.url.startswith("https://")
    assert created.expires_at == NOW + PAYMENT_LINK_LIFETIME
    state = await provider.get_payment(created.payment_id)
    assert state.status is PaymentStatus.PENDING
    assert (state.amount, state.currency, state.order_id, state.test) == (
        Decimal("405"),
        "RUB",
        1,
        True,
    )
    assert state.paid_at is None


async def test_same_idempotency_key_gives_same_payment(provider: FakePaymentProvider) -> None:
    first = await provider.create_payment(request())
    second = await provider.create_payment(request())

    assert first == second
    assert len(provider.ledger.all()) == 1


async def test_same_key_with_other_amount_is_rejected(provider: FakePaymentProvider) -> None:
    await provider.create_payment(request())
    with pytest.raises(PaymentRejected):
        await provider.create_payment(request(amount="150"))


async def test_pay_and_cancel(provider: FakePaymentProvider, clock: FakeClock) -> None:
    paid = await provider.create_payment(request(key="a"))
    canceled = await provider.create_payment(request(order_id=2, key="b"))
    clock.advance(timedelta(minutes=3))

    provider.ledger.pay(paid.payment_id, now=clock())
    provider.ledger.cancel(canceled.payment_id)

    paid_state = await provider.get_payment(paid.payment_id)
    assert (paid_state.status, paid_state.paid_at) == (PaymentStatus.SUCCEEDED, clock())
    assert (await provider.get_payment(canceled.payment_id)).status is PaymentStatus.CANCELED


async def test_pay_other_amount(provider: FakePaymentProvider, clock: FakeClock) -> None:
    created = await provider.create_payment(request())
    provider.ledger.pay(created.payment_id, now=clock(), amount=Decimal("400"))

    assert (await provider.get_payment(created.payment_id)).amount == Decimal("400")


async def test_unpaid_payment_expires(provider: FakePaymentProvider, clock: FakeClock) -> None:
    created = await provider.create_payment(request())
    clock.advance(PAYMENT_LINK_LIFETIME)

    assert (await provider.get_payment(created.payment_id)).status is PaymentStatus.CANCELED
    with pytest.raises(PaymentRejected):
        provider.ledger.pay(created.payment_id, now=clock())


async def test_unknown_payment(provider: FakePaymentProvider) -> None:
    with pytest.raises(PaymentNotFound):
        await provider.get_payment("fake_nope")


async def test_refund_part_then_rest(provider: FakePaymentProvider, clock: FakeClock) -> None:
    created = await provider.create_payment(request())
    provider.ledger.pay(created.payment_id, now=clock())

    first = await provider.refund(created.payment_id, Decimal("100"), "r1")
    again = await provider.refund(created.payment_id, Decimal("100"), "r1")  # повтор запроса
    rest = await provider.refund(created.payment_id, Decimal("305"), "r2")

    assert first == again
    assert first.outcome is RefundOutcome.SUCCEEDED
    assert rest.amount == Decimal("305")
    with pytest.raises(PaymentRejected):
        await provider.refund(created.payment_id, Decimal("1"), "r3")


async def test_refund_of_unpaid_is_rejected(provider: FakePaymentProvider) -> None:
    created = await provider.create_payment(request())
    with pytest.raises(PaymentRejected):
        await provider.refund(created.payment_id, Decimal("1"), "r1")


async def test_fail_next(provider: FakePaymentProvider) -> None:
    provider.fail_next(PaymentUnavailable("платёжка лежит"))
    with pytest.raises(PaymentUnavailable):
        await provider.create_payment(request())

    await provider.create_payment(request())
    assert len(provider.ledger.all()) == 1


async def test_file_ledger_is_shared_with_script(tmp_path: Path, clock: FakeClock) -> None:
    """Бот и `simulate_payment.py` — разные процессы: общий у них только файл."""
    path = tmp_path / "data" / "fake_payments.json"
    bot_side = FakePaymentProvider(clock, FakePaymentLedger(path))
    created = await bot_side.create_payment(request(order_id=7))

    script_side = FakePaymentLedger(path)
    [found] = script_side.find_by_order(7)
    script_side.pay(found.payment_id, now=clock())

    assert (await bot_side.get_payment(created.payment_id)).status is PaymentStatus.SUCCEEDED
