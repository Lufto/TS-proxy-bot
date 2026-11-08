"""Карточка клиента в теме обращения: подписка, устройства, оплаты, пробный."""

from datetime import timedelta
from decimal import Decimal

import pytest
from conftest import NOW
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tsproxy.config import RunMode
from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import OrderStatus
from tsproxy.db.models import Client, Order
from tsproxy.domain.profile import ProfileStatus
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import (
    Device,
    RemnawaveError,
    RemnawaveResponseError,
    RemnawaveUnavailable,
)
from tsproxy.services.client_card import ClientCardService, PaymentsSummary
from tsproxy.services.fakes import FakeClock
from tsproxy.services.profile import DeviceSlots, ProfileService

EXPIRE_AT = NOW + timedelta(days=20)


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


@pytest.fixture
def cards(engine: AsyncEngine, remnawave: FakeRemnawave, clock: FakeClock) -> ClientCardService:
    return ClientCardService(create_session_factory(engine), ProfileService(remnawave, clock))


def order(client: Client, *, paid: str | None, paid_at_days_ago: int = 0) -> Order:
    return Order(
        client_id=client.id,
        tariff_id="m1",
        price=Decimal(150),
        days=30,
        status=OrderStatus.ACCESS_GRANTED if paid else OrderStatus.CREATED,
        created_at=NOW,
        paid_amount=None if paid is None else Decimal(paid),
        paid_at=None if paid is None else NOW - timedelta(days=paid_at_days_ago),
    )


async def test_card_of_client_without_subscription(
    cards: ClientCardService, client: Client
) -> None:
    card = await cards.card(client.telegram_id)

    assert card is not None
    assert (card.telegram_id, card.name, card.username) == (client.telegram_id, "Иван", None)
    assert card.profile is None
    assert card.panel_failed is False
    assert card.payments == PaymentsSummary(count=0, total=Decimal(0), last_paid_at=None)
    assert card.trial_granted_at is None


async def test_card_shows_subscription_devices_payments_and_trial(
    cards: ClientCardService,
    session: AsyncSession,
    client: Client,
    remnawave: FakeRemnawave,
) -> None:
    client.trial_granted_at = NOW - timedelta(days=40)
    session.add_all(
        [
            order(client, paid="150", paid_at_days_ago=35),
            order(client, paid="405", paid_at_days_ago=2),
            order(client, paid=None),  # не оплачен — не считается
        ]
    )
    await session.commit()
    await remnawave.get_or_create_account(
        client.telegram_id, expire_at=EXPIRE_AT, device_limit=3, traffic_limit_bytes=0
    )
    remnawave.simulate_device(
        client.telegram_id,
        Device(hwid="hw-1", platform="iOS", os_version="18", device_model="iPhone", created_at=NOW),
    )

    card = await cards.card(client.telegram_id)

    assert card is not None
    assert card.profile is not None
    assert (card.profile.status, card.profile.expire_at) == (ProfileStatus.ACTIVE, EXPIRE_AT)
    assert card.profile.devices == DeviceSlots(used=1, limit=3)
    assert card.payments == PaymentsSummary(
        count=2, total=Decimal("555"), last_paid_at=NOW - timedelta(days=2)
    )
    assert card.trial_granted_at == NOW - timedelta(days=40)


@pytest.mark.parametrize(
    "error", [RemnawaveUnavailable("таймаут"), RemnawaveResponseError("другая версия")]
)
async def test_panel_failure_does_not_break_the_card(
    cards: ClientCardService, client: Client, remnawave: FakeRemnawave, error: RemnawaveError
) -> None:
    """Карточка — подсказка администратору: без панели она всё равно нужна."""
    remnawave.fail_next(error)

    card = await cards.card(client.telegram_id)

    assert card is not None
    assert (card.profile, card.panel_failed) == (None, True)


async def test_unknown_client_has_no_card(cards: ClientCardService) -> None:
    assert await cards.card(404) is None
