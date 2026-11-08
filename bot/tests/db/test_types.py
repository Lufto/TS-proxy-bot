"""Время хранится в UTC и читается с часовым поясом; деньги — Decimal."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from tsproxy.db.models import Client, Order

MOSCOW = timezone(timedelta(hours=3))


async def test_datetime_round_trip_returns_aware_utc(session: AsyncSession) -> None:
    moscow_time = datetime(2026, 10, 24, 15, 30, 45, 123456, tzinfo=MOSCOW)
    session.add(Client(telegram_id=1, first_seen_at=moscow_time))
    await session.commit()
    session.expunge_all()

    loaded = (await session.execute(select(Client))).scalar_one()

    assert loaded.first_seen_at == moscow_time
    assert loaded.first_seen_at.tzinfo is UTC
    assert loaded.first_seen_at.hour == 12


async def test_datetime_stored_as_utc(session: AsyncSession) -> None:
    session.add(Client(telegram_id=1, first_seen_at=datetime(2026, 1, 1, 3, 0, tzinfo=MOSCOW)))
    await session.commit()

    raw = (await session.execute(text("SELECT first_seen_at FROM clients"))).scalar_one()

    assert raw.startswith("2026-01-01 00:00:00")


async def test_naive_datetime_rejected(session: AsyncSession) -> None:
    session.add(Client(telegram_id=1, first_seen_at=datetime(2026, 1, 1)))  # noqa: DTZ001

    with pytest.raises(StatementError, match="без часового пояса"):
        await session.commit()


async def test_money_round_trip_is_decimal(session: AsyncSession, client: Client) -> None:
    session.add(Order(client_id=client.id, tariff_id="m3", price=Decimal("405.00"), days=90))
    await session.commit()
    session.expunge_all()

    order = (await session.execute(select(Order))).scalar_one()

    assert order.price == Decimal("405.00")
    assert isinstance(order.price, Decimal)
    assert order.created_at.tzinfo is UTC
