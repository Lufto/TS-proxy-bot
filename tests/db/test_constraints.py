"""Ограничения базы: «ровно один раз» держит сама база, а не только код."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tsproxy.db.enums import NotificationKind
from tsproxy.db.models import Client, Event, NodeOutage, Notification, Order
from tsproxy.db.repository import claim_outage_compensation, claim_trial

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
PRICE = Decimal("405.00")


def paid_order(client_id: int, payment_id: str) -> Order:
    return Order(
        client_id=client_id,
        tariff_id="m3",
        price=PRICE,
        days=90,
        payment_provider="yookassa",
        payment_id=payment_id,
    )


async def test_same_payment_cannot_credit_two_orders(session: AsyncSession, client: Client) -> None:
    session.add(paid_order(client.id, "pay-1"))
    await session.commit()

    session.add(paid_order(client.id, "pay-1"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_orders_without_payment_yet_are_allowed(
    session: AsyncSession, client: Client
) -> None:
    session.add_all([paid_order(client.id, None), paid_order(client.id, None)])  # type: ignore[arg-type]
    await session.commit()


async def test_order_idempotency_keys_are_unique(session: AsyncSession, client: Client) -> None:
    first, second = paid_order(client.id, "a"), paid_order(client.id, "b")
    session.add_all([first, second])
    await session.commit()

    assert first.idempotency_key != second.idempotency_key


async def test_second_client_row_for_same_telegram_id_rejected(
    session: AsyncSession, client: Client
) -> None:
    session.add(Client(telegram_id=client.telegram_id, trial_granted_at=NOW))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_trial_claimed_only_once(session: AsyncSession, client: Client) -> None:
    assert await claim_trial(session, client.id, NOW) is True
    assert await claim_trial(session, client.id, NOW + timedelta(days=1)) is False
    await session.commit()

    await session.refresh(client)
    assert client.trial_granted_at == NOW


async def test_notification_sent_once_per_expiry(session: AsyncSession, client: Client) -> None:
    session.add(
        Notification(client_id=client.id, kind=NotificationKind.DAY_BEFORE, related_expire_at=NOW)
    )
    await session.commit()

    session.add(
        Notification(client_id=client.id, kind=NotificationKind.DAY_BEFORE, related_expire_at=NOW)
    )
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_same_notification_for_new_expiry_allowed(
    session: AsyncSession, client: Client
) -> None:
    session.add_all(
        [
            Notification(
                client_id=client.id, kind=NotificationKind.DAY_BEFORE, related_expire_at=NOW
            ),
            Notification(
                client_id=client.id,
                kind=NotificationKind.DAY_BEFORE,
                related_expire_at=NOW + timedelta(days=30),
            ),
        ]
    )
    await session.commit()


async def test_outage_compensated_only_once(session: AsyncSession) -> None:
    outage = NodeOutage(node_uuid="node-1", started_at=NOW, ended_at=NOW + timedelta(hours=2))
    session.add(outage)
    await session.commit()

    assert await claim_outage_compensation(session, outage.id, admin_id=7, now=NOW) is True
    assert await claim_outage_compensation(session, outage.id, admin_id=8, now=NOW) is False
    await session.commit()

    await session.refresh(outage)
    assert outage.compensated_by_admin_id == 7


async def test_same_outage_not_recorded_twice(session: AsyncSession) -> None:
    session.add(NodeOutage(node_uuid="node-1", started_at=NOW))
    await session.commit()

    session.add(NodeOutage(node_uuid="node-1", started_at=NOW))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_foreign_keys_enforced(session: AsyncSession) -> None:
    session.add(paid_order(client_id=999, payment_id="x"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_event_glue_defaults(session: AsyncSession) -> None:
    event = Event(kind="fake_payment_notification", glue_key="fake:1.2.3.4", details={"ip": "x"})
    session.add(event)
    await session.commit()

    assert event.repeat_count == 1
    assert event.last_seen_at is None
    assert event.details == {"ip": "x"}
