"""Возврат оплаты своим способом: платёжка — через `refund`, звёзды — `refundStarPayment`
(задача 33). Telegram — подделка, запросы только запоминаются."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramServerError,
)
from aiogram.methods import RefundStarPayment

from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.integrations.fake_payments import FakePaymentProvider
from tsproxy.integrations.payments_types import (
    PaidPayment,
    PaymentRejected,
    PaymentRequest,
    PaymentUnavailable,
    RefundOutcome,
)
from tsproxy.integrations.refunds import ProviderRefunds, RefundRouter
from tsproxy.integrations.telegram_stars import STARS_PROVIDER, TelegramStarsRefunds

NOW = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
CLIENT = 111
CHARGE = "stxCharge123"
PRICE = Decimal(150)


@pytest.fixture
def telegram() -> FakeTelegramSession:
    return FakeTelegramSession()


@pytest.fixture
def stars(telegram: FakeTelegramSession) -> TelegramStarsRefunds:
    return TelegramStarsRefunds(Bot("42:TESTtokenTESTtokenTESTtoken", session=telegram))


def paid_stars() -> PaidPayment:
    return PaidPayment(STARS_PROVIDER, CHARGE, CLIENT)


async def test_stars_are_refunded_by_client_and_charge(
    stars: TelegramStarsRefunds, telegram: FakeTelegramSession
) -> None:
    refund = await stars.refund(paid_stars(), PRICE, "refund-request-1")

    [sent] = telegram.sent(RefundStarPayment)
    assert (sent.user_id, sent.telegram_payment_charge_id) == (CLIENT, CHARGE)
    assert refund.outcome is RefundOutcome.SUCCEEDED
    assert (refund.payment_id, refund.amount) == (CHARGE, PRICE)


async def test_already_refunded_charge_counts_as_refunded(
    stars: TelegramStarsRefunds, telegram: FakeTelegramSession
) -> None:
    # повтор после сбоя: звёзды уже вернулись в прошлый раз — второй раз не вернутся
    telegram.fail_next(
        RefundStarPayment, TelegramBadRequest, "Bad Request: CHARGE_ALREADY_REFUNDED"
    )

    refund = await stars.refund(paid_stars(), PRICE, "refund-request-1")

    assert refund.outcome is RefundOutcome.SUCCEEDED


@pytest.mark.parametrize(
    "error",
    [
        (TelegramBadRequest, "Bad Request: CHARGE_NOT_FOUND"),
        (TelegramForbiddenError, "Forbidden: bot was blocked by the user"),
    ],
)
async def test_refused_refund_is_rejection(
    stars: TelegramStarsRefunds, telegram: FakeTelegramSession, error: tuple[type, str]
) -> None:
    telegram.fail_next(RefundStarPayment, *error)

    with pytest.raises(PaymentRejected):
        await stars.refund(paid_stars(), PRICE, "refund-request-1")


@pytest.mark.parametrize(
    "error",
    [
        (TelegramNetworkError, "timeout"),
        (TelegramServerError, "Internal Server Error"),
    ],
)
async def test_telegram_down_is_retryable(
    stars: TelegramStarsRefunds, telegram: FakeTelegramSession, error: tuple[type, str]
) -> None:
    telegram.fail_next(RefundStarPayment, *error)

    with pytest.raises(PaymentUnavailable):
        await stars.refund(paid_stars(), PRICE, "refund-request-1")


# --- Маршрут возврата по платёжке заказа ---


async def test_router_sends_each_payment_to_its_provider(
    stars: TelegramStarsRefunds, telegram: FakeTelegramSession
) -> None:
    provider = FakePaymentProvider(lambda: NOW)
    created = await provider.create_payment(PaymentRequest(1, PRICE, "Подписка", "key-1"))
    provider.ledger.pay(created.payment_id, NOW)
    router = RefundRouter({provider.name: ProviderRefunds(provider), STARS_PROVIDER: stars})

    by_provider = await router.refund(
        PaidPayment(provider.name, created.payment_id, CLIENT), PRICE, "refund-request-1"
    )
    by_stars = await router.refund(paid_stars(), PRICE, "refund-request-2")

    assert by_provider.outcome is RefundOutcome.SUCCEEDED
    assert provider.ledger.find_by_order(1)[0].refunded == PRICE
    assert by_stars.payment_id == CHARGE
    assert len(telegram.sent(RefundStarPayment)) == 1


async def test_router_knows_which_payments_it_can_refund(stars: TelegramStarsRefunds) -> None:
    router = RefundRouter({STARS_PROVIDER: stars})

    assert router.supports(STARS_PROVIDER)
    assert not router.supports("fake")
    assert not router.supports(None)
    with pytest.raises(PaymentRejected, match="fake"):
        await router.refund(PaidPayment("fake", "fake_1", CLIENT), PRICE, "refund-request-1")
