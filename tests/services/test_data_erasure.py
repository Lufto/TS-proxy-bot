"""Удаление данных клиента по запросу (задача 24).

Удаляется: имя, @username, переписка, обращения и их темы в группе. Остаются обезличенными
заказы и журнал событий (бессрочно), а также Telegram ID и отметка пробного — чтобы тот же
Telegram ID не получил второй пробный.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import MessageAuthor, OrderStatus, TicketStatus
from tsproxy.db.models import Client, Event, Order, SupportTicket, TicketMessage
from tsproxy.services.clients import ClientService
from tsproxy.services.erasure import (
    ERASE_ACTION,
    DataErasureService,
    ErasureOutcome,
    TopicRemovalFailed,
)
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeClock, FakeTopicRemover
from tsproxy.services.masking import Masker

TELEGRAM_ID = 111  # клиент из фикстуры `client`
OTHER_TELEGRAM_ID = 222
ADMIN_ID = 900
FIRST_TOPIC = 501
SECOND_TOPIC = 502
OTHER_TOPIC = 601


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def topics() -> FakeTopicRemover:
    return FakeTopicRemover()


@pytest.fixture
def events(clock: FakeClock, mask: Masker) -> EventLog:
    return EventLog(clock, mask)


@pytest.fixture
def erasure(
    session_factory: async_sessionmaker[AsyncSession], topics: FakeTopicRemover, events: EventLog
) -> DataErasureService:
    return DataErasureService(session_factory, topics, events)


async def add_ticket(
    session: AsyncSession, client: Client, clock: FakeClock, topic_id: int | None, *texts: str
) -> SupportTicket:
    """Обращение клиента с перепиской: первое сообщение — клиента, дальше — поддержки."""
    ticket = SupportTicket(
        client_id=client.id, topic_id=topic_id, opened_at=clock.now, status=TicketStatus.CLOSED
    )
    session.add(ticket)
    await session.flush()
    for number, text in enumerate(texts):
        author = MessageAuthor.CLIENT if number == 0 else MessageAuthor.SUPPORT
        session.add(
            TicketMessage(
                ticket_id=ticket.id,
                author=author,
                text=text,
                file_id="photo-file-id",
                file_kind="photo",
                created_at=clock.now,
            )
        )
    await session.commit()
    return ticket


async def add_paid_order(session: AsyncSession, client: Client, clock: FakeClock) -> Order:
    order = Order(
        client_id=client.id,
        tariff_id="m1",
        price=Decimal("150.00"),
        days=30,
        status=OrderStatus.ACCESS_GRANTED,
        paid_amount=Decimal("150.00"),
        paid_at=clock.now,
    )
    session.add(order)
    await session.commit()
    return order


@pytest.fixture
async def known_client(session: AsyncSession, client: Client, clock: FakeClock) -> Client:
    """Клиент с именем, @username, пробным, оплатой и двумя обращениями."""
    client.username = "ivan_petrov"
    client.trial_granted_at = clock.now - timedelta(days=40)
    session.add(client)
    await session.commit()
    await add_ticket(session, client, clock, FIRST_TOPIC, "Не подключается", "Обновите подписку")
    await add_ticket(session, client, clock, SECOND_TOPIC, "Верните деньги")
    await add_paid_order(session, client, clock)
    return client


@pytest.fixture
async def other_client(session: AsyncSession, clock: FakeClock) -> Client:
    other = Client(
        telegram_id=OTHER_TELEGRAM_ID, name="Пётр", username="petr", first_seen_at=clock.now
    )
    session.add(other)
    await session.commit()
    await add_ticket(session, other, clock, OTHER_TOPIC, "Чужой вопрос")
    return other


async def fresh_client(session: AsyncSession, telegram_id: int) -> Client:
    found = await session.scalar(
        select(Client).where(Client.telegram_id == telegram_id),
        execution_options={"populate_existing": True},
    )
    assert found is not None
    return found


async def correspondence(session: AsyncSession, client_id: int) -> list[TicketMessage]:
    query = (
        select(TicketMessage)
        .join(SupportTicket, SupportTicket.id == TicketMessage.ticket_id)
        .where(SupportTicket.client_id == client_id)
    )
    return list(await session.scalars(query))


async def tickets_of(session: AsyncSession, client_id: int) -> list[SupportTicket]:
    return list(
        await session.scalars(select(SupportTicket).where(SupportTicket.client_id == client_id))
    )


async def erase_events(session: AsyncSession) -> list[Event]:
    query = select(Event).where(Event.kind == EventKind.ADMIN_ACTION.value).order_by(Event.id)
    return [
        e for e in await session.scalars(query) if (e.details or {}).get("action") == ERASE_ACTION
    ]


# --- Предпросмотр ---


async def test_preview_shows_what_will_be_deleted_and_kept(
    erasure: DataErasureService, known_client: Client
) -> None:
    preview = await erasure.preview(TELEGRAM_ID)

    assert preview is not None
    assert (preview.name, preview.username) == ("Иван", "ivan_petrov")
    assert (preview.tickets, preview.messages, preview.topics) == (2, 3, 2)
    assert preview.orders == 1


async def test_preview_changes_nothing(
    erasure: DataErasureService,
    session: AsyncSession,
    known_client: Client,
    topics: FakeTopicRemover,
) -> None:
    await erasure.preview(TELEGRAM_ID)

    client = await fresh_client(session, TELEGRAM_ID)
    assert (client.name, client.username) == ("Иван", "ivan_petrov")
    assert len(await correspondence(session, client.id)) == 3
    assert topics.removed == []


async def test_preview_of_unknown_client_is_none(erasure: DataErasureService) -> None:
    assert await erasure.preview(OTHER_TELEGRAM_ID) is None


# --- Удаление ---


async def test_after_erasure_client_has_no_name_username_or_correspondence(
    erasure: DataErasureService, session: AsyncSession, known_client: Client
) -> None:
    result = await erasure.erase(ADMIN_ID, TELEGRAM_ID)

    assert result.outcome is ErasureOutcome.ERASED
    client = await fresh_client(session, TELEGRAM_ID)
    assert client.name is None
    assert client.username is None
    assert await correspondence(session, client.id) == []
    assert await tickets_of(session, client.id) == []


async def test_erasure_deletes_support_topics_in_the_group(
    erasure: DataErasureService, known_client: Client, topics: FakeTopicRemover
) -> None:
    result = await erasure.erase(ADMIN_ID, TELEGRAM_ID)

    assert sorted(topics.removed) == [FIRST_TOPIC, SECOND_TOPIC]
    assert (result.tickets, result.messages, result.topics) == (2, 3, 2)


async def test_ticket_without_topic_needs_no_topic_removal(
    erasure: DataErasureService,
    session: AsyncSession,
    client: Client,
    clock: FakeClock,
    topics: FakeTopicRemover,
) -> None:
    await add_ticket(session, client, clock, None, "Тема не создалась")

    result = await erasure.erase(ADMIN_ID, TELEGRAM_ID)

    assert topics.removed == []
    assert (result.tickets, result.messages, result.topics) == (1, 1, 0)
    assert await correspondence(session, client.id) == []


async def test_orders_and_journal_stay_without_personal_data(
    erasure: DataErasureService, session: AsyncSession, known_client: Client, events: EventLog
) -> None:
    await events.record(
        session, EventKind.PAYMENT_RECEIVED, client_id=known_client.id, details={"amount": "150"}
    )
    await session.commit()

    await erasure.erase(ADMIN_ID, TELEGRAM_ID)

    orders = list(await session.scalars(select(Order).where(Order.client_id == known_client.id)))
    assert len(orders) == 1
    payment_events = await session.scalar(
        select(func.count()).where(
            Event.kind == EventKind.PAYMENT_RECEIVED.value, Event.client_id == known_client.id
        )
    )
    assert payment_events == 1
    # в журнале нигде нет имени, @username и текста переписки
    for event in await session.scalars(select(Event)):
        stored = str(event.details)
        assert "Иван" not in stored
        assert "ivan_petrov" not in stored
        assert "Не подключается" not in stored


async def test_erasure_is_an_admin_action_in_the_journal(
    erasure: DataErasureService, session: AsyncSession, known_client: Client
) -> None:
    await erasure.erase(ADMIN_ID, TELEGRAM_ID)

    [event] = await erase_events(session)
    assert (event.client_id, event.admin_id) == (known_client.id, ADMIN_ID)
    assert event.details == {"action": ERASE_ACTION, "tickets": 2, "messages": 3, "topics": 2}


async def test_other_clients_data_is_untouched(
    erasure: DataErasureService,
    session: AsyncSession,
    known_client: Client,
    other_client: Client,
    topics: FakeTopicRemover,
) -> None:
    await erasure.erase(ADMIN_ID, TELEGRAM_ID)

    other = await fresh_client(session, OTHER_TELEGRAM_ID)
    assert (other.name, other.username) == ("Пётр", "petr")
    assert len(await correspondence(session, other.id)) == 1
    assert OTHER_TOPIC not in topics.removed


async def test_telegram_id_and_trial_mark_stay_so_no_second_trial(
    erasure: DataErasureService,
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    known_client: Client,
    clock: FakeClock,
) -> None:
    granted_at = known_client.trial_granted_at

    await erasure.erase(ADMIN_ID, TELEGRAM_ID)

    client = await fresh_client(session, TELEGRAM_ID)
    assert client.id == known_client.id
    assert client.trial_granted_at == granted_at
    assert await ClientService(session_factory, clock).trial_offered(client) is False


async def test_topic_removal_failure_deletes_nothing(
    erasure: DataErasureService,
    session: AsyncSession,
    known_client: Client,
    topics: FakeTopicRemover,
) -> None:
    topics.fail_on = SECOND_TOPIC
    topics.error = TopicRemovalFailed("not enough rights to manage topics")

    with pytest.raises(TopicRemovalFailed):
        await erasure.erase(ADMIN_ID, TELEGRAM_ID)

    client = await fresh_client(session, TELEGRAM_ID)
    assert (client.name, client.username) == ("Иван", "ivan_petrov")
    assert len(await correspondence(session, client.id)) == 3
    assert await erase_events(session) == []


async def test_retry_after_failure_finishes_the_erasure(
    erasure: DataErasureService,
    session: AsyncSession,
    known_client: Client,
    topics: FakeTopicRemover,
) -> None:
    topics.fail_on = SECOND_TOPIC
    topics.error = TopicRemovalFailed("network")
    with pytest.raises(TopicRemovalFailed):
        await erasure.erase(ADMIN_ID, TELEGRAM_ID)
    topics.error = None

    result = await erasure.erase(ADMIN_ID, TELEGRAM_ID)

    assert result.outcome is ErasureOutcome.ERASED
    assert await correspondence(session, known_client.id) == []


async def test_second_erasure_finds_nothing_and_writes_no_event(
    erasure: DataErasureService, session: AsyncSession, known_client: Client
) -> None:
    await erasure.erase(ADMIN_ID, TELEGRAM_ID)

    again = await erasure.erase(ADMIN_ID, TELEGRAM_ID)

    assert again.outcome is ErasureOutcome.NOTHING_TO_ERASE
    assert len(await erase_events(session)) == 1


async def test_erasure_of_unknown_client(erasure: DataErasureService) -> None:
    result = await erasure.erase(ADMIN_ID, OTHER_TELEGRAM_ID)

    assert result.outcome is ErasureOutcome.CLIENT_NOT_FOUND
