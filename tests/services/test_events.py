"""Журнал событий: запись, склейка повторов, очистка подробностей от секретов."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tsproxy.db.models import Client, Event
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeClock
from tsproxy.services.masking import Masker

WINDOW = timedelta(hours=1)
SUBSCRIPTION_URL = "https://sub.ts-proxy.com/AbCdEfGh12345678"
SUBSCRIPTION_CODE_HEAD = "AbCdEfGh1234"  # в маскированной ссылке остаются только 4 последних
FAKE_KEY = "fake_payment:order:42"


@pytest.fixture
def log(clock: FakeClock, mask: Masker) -> EventLog:
    return EventLog(clock=clock, mask=mask, glue_window=WINDOW)


async def rows(session: AsyncSession) -> list[Event]:
    return list(await session.scalars(select(Event).order_by(Event.id)))


async def count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(Event)) or 0


async def test_record_writes_event_with_links(
    session: AsyncSession, client: Client, clock: FakeClock, log: EventLog
) -> None:
    event = await log.record(
        session,
        EventKind.TRIAL_GRANTED,
        client_id=client.id,
        admin_id=555,
        details={"days": 3},
    )
    await session.commit()

    [saved] = await rows(session)
    assert saved.id == event.id
    assert saved.kind == "trial_granted"
    assert saved.client_id == client.id
    assert saved.admin_id == 555
    assert saved.details == {"days": 3}
    assert saved.occurred_at == clock.now
    assert saved.repeat_count == 1
    assert saved.last_seen_at is None


async def test_events_without_glue_key_are_never_glued(
    session: AsyncSession, log: EventLog
) -> None:
    for _ in range(3):
        await log.record(session, EventKind.ADMIN_ACTION, admin_id=555)
    await session.commit()
    assert await count(session) == 3


async def test_same_glue_key_within_window_is_glued(
    session: AsyncSession, clock: FakeClock, log: EventLog
) -> None:
    first_at = clock.now
    for _ in range(5):
        await log.record(session, EventKind.FAKE_PAYMENT_NOTIFICATION, glue_key=FAKE_KEY)
        clock.advance(timedelta(minutes=10))
    await session.commit()

    [event] = await rows(session)
    assert event.repeat_count == 5
    assert event.occurred_at == first_at
    assert event.last_seen_at == first_at + timedelta(minutes=40)


async def test_glued_event_returns_updated_row(session: AsyncSession, log: EventLog) -> None:
    first = await log.record(session, EventKind.FAKE_PAYMENT_NOTIFICATION, glue_key=FAKE_KEY)
    second = await log.record(session, EventKind.FAKE_PAYMENT_NOTIFICATION, glue_key=FAKE_KEY)
    assert second.id == first.id
    assert second.repeat_count == 2


async def test_different_glue_keys_are_not_glued(session: AsyncSession, log: EventLog) -> None:
    await log.record(session, EventKind.FAKE_PAYMENT_NOTIFICATION, glue_key="fake:1")
    await log.record(session, EventKind.FAKE_PAYMENT_NOTIFICATION, glue_key="fake:2")
    await session.commit()
    assert [e.repeat_count for e in await rows(session)] == [1, 1]


async def test_same_glue_key_of_different_kind_is_not_glued(
    session: AsyncSession, log: EventLog
) -> None:
    await log.record(session, EventKind.FAKE_PAYMENT_NOTIFICATION, glue_key=FAKE_KEY)
    await log.record(session, EventKind.ACCESS_FAILED, glue_key=FAKE_KEY)
    await session.commit()
    assert await count(session) == 2


async def test_window_counts_from_first_record_and_expiry_starts_new_row(
    session: AsyncSession, clock: FakeClock, log: EventLog
) -> None:
    await log.record(session, EventKind.FAKE_PAYMENT_NOTIFICATION, glue_key=FAKE_KEY)
    clock.advance(WINDOW - timedelta(seconds=1))
    await log.record(session, EventKind.FAKE_PAYMENT_NOTIFICATION, glue_key=FAKE_KEY)
    # окно считается от первой записи, а не от последнего повтора
    clock.advance(timedelta(seconds=1))
    await log.record(session, EventKind.FAKE_PAYMENT_NOTIFICATION, glue_key=FAKE_KEY)
    clock.advance(timedelta(minutes=1))
    await log.record(session, EventKind.FAKE_PAYMENT_NOTIFICATION, glue_key=FAKE_KEY)
    await session.commit()

    assert [e.repeat_count for e in await rows(session)] == [2, 2]


async def test_secrets_and_subscription_urls_are_masked_in_details(
    session: AsyncSession, log: EventLog, bot_token: str
) -> None:
    await log.record(
        session,
        EventKind.ACCESS_GRANTED,
        details={
            "message": f"выдана ссылка {SUBSCRIPTION_URL}",
            "request": {"url": f"https://api.telegram.org/bot{bot_token}/sendMessage"},
            "links": [SUBSCRIPTION_URL],
            "authorization": "Bearer abc.def",
            "remnawave_token": "not-from-env-but-still-secret",
        },
    )
    await session.commit()

    [event] = await rows(session)
    stored = str(event.details)
    assert SUBSCRIPTION_CODE_HEAD not in stored
    assert bot_token not in stored
    assert "abc.def" not in stored
    assert "not-from-env" not in stored
    assert event.details is not None
    assert event.details["links"] == ["https://…/****5678"]


async def test_money_time_and_enums_become_json(session: AsyncSession, log: EventLog) -> None:
    paid_at = datetime(2026, 10, 24, 9, 30, tzinfo=UTC)
    await log.record(
        session,
        EventKind.PAYMENT_RECEIVED,
        details={"amount": Decimal("405.00"), "paid_at": paid_at, "kind": EventKind.ORDER_CREATED},
    )
    await session.commit()

    [event] = await rows(session)
    assert event.details == {
        "amount": "405.00",
        "paid_at": "2026-10-24T09:30:00+00:00",
        "kind": "order_created",
    }


async def test_unknown_detail_type_is_rejected(session: AsyncSession, log: EventLog) -> None:
    with pytest.raises(TypeError, match="object"):
        await log.record(session, EventKind.ADMIN_ACTION, details={"raw": object()})


async def test_naive_clock_is_rejected(session: AsyncSession, mask: Masker) -> None:
    naive = EventLog(clock=lambda: datetime(2026, 10, 24, 12, 0), mask=mask)  # noqa: DTZ001
    with pytest.raises(ValueError, match="часового пояса"):
        await naive.record(session, EventKind.ADMIN_ACTION)


async def test_too_long_glue_key_is_rejected(session: AsyncSession, log: EventLog) -> None:
    with pytest.raises(ValueError, match="Ключ склейки"):
        await log.record(session, EventKind.ADMIN_ACTION, glue_key="x" * 1000)
