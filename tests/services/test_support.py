"""Обращения в поддержку: открыть или продолжить, тема в группе, переписка в базе."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import MessageAuthor, TicketStatus
from tsproxy.db.models import Client, SupportTicket, TicketMessage
from tsproxy.services.fakes import FakeClock
from tsproxy.services.masking import Masker
from tsproxy.services.support import MessageContent, SupportService

TOPIC_ID = 501
SUBSCRIPTION_URL = "https://sub.ts-proxy.com/AbCdEfGh12345678"
SUBSCRIPTION_CODE_HEAD = "AbCdEfGh1234"


@pytest.fixture
def support(engine: AsyncEngine, clock: FakeClock, mask: Masker) -> SupportService:
    return SupportService(create_session_factory(engine), clock, mask)


async def messages(session: AsyncSession) -> list[TicketMessage]:
    query = select(TicketMessage).order_by(TicketMessage.id)
    return list(await session.scalars(query, execution_options={"populate_existing": True}))


async def stored_ticket(session: AsyncSession, ticket_id: int) -> SupportTicket:
    ticket = await session.get(SupportTicket, ticket_id, populate_existing=True)
    assert ticket is not None
    return ticket


async def test_first_message_opens_a_ticket(
    support: SupportService, session: AsyncSession, client: Client, clock: FakeClock
) -> None:
    ticket = await support.current_ticket(client.id)

    assert ticket.created is True
    assert ticket.topic_id is None  # темы ещё нет — её создаёт бот
    assert ticket.client_telegram_id == client.telegram_id
    saved = await stored_ticket(session, ticket.id)
    assert (saved.client_id, saved.status, saved.opened_at) == (
        client.id,
        TicketStatus.OPEN,
        clock.now,
    )


async def test_next_message_continues_the_open_ticket(
    support: SupportService, client: Client, clock: FakeClock
) -> None:
    first = await support.current_ticket(client.id)
    await support.attach_topic(first.id, TOPIC_ID)
    clock.advance(timedelta(days=3))

    again = await support.current_ticket(client.id)

    assert (again.id, again.created, again.topic_id) == (first.id, False, TOPIC_ID)


async def test_tickets_of_different_clients_are_separate(
    support: SupportService, session: AsyncSession, client: Client, clock: FakeClock
) -> None:
    other = Client(telegram_id=222, first_seen_at=clock.now)
    session.add(other)
    await session.commit()

    mine = await support.current_ticket(client.id)
    theirs = await support.current_ticket(other.id)

    assert mine.id != theirs.id
    assert theirs.client_telegram_id == 222


async def test_ticket_is_found_by_its_topic(support: SupportService, client: Client) -> None:
    ticket = await support.current_ticket(client.id)
    await support.attach_topic(ticket.id, TOPIC_ID)

    found = await support.ticket_by_topic(TOPIC_ID)

    assert found is not None
    assert (found.id, found.client_telegram_id, found.created) == (
        ticket.id,
        client.telegram_id,
        False,
    )
    assert await support.ticket_by_topic(TOPIC_ID + 1) is None  # «🚨 Тревоги» и другие темы


async def test_client_message_is_stored_with_both_message_ids(
    support: SupportService, session: AsyncSession, client: Client, clock: FakeClock
) -> None:
    ticket = await support.current_ticket(client.id)

    await support.record_message(
        ticket.id,
        MessageAuthor.CLIENT,
        MessageContent(text="Скриншот ошибки", file_id="photo-file-id", file_kind="photo"),
        client_message_id=10,
        group_message_id=2001,
    )

    [saved] = await messages(session)
    assert (saved.ticket_id, saved.author, saved.text) == (
        ticket.id,
        MessageAuthor.CLIENT,
        "Скриншот ошибки",
    )
    assert (saved.file_id, saved.file_kind) == ("photo-file-id", "photo")
    assert (saved.client_message_id, saved.group_message_id) == (10, 2001)
    assert saved.created_at == clock.now


async def test_subscription_link_is_stored_masked(
    support: SupportService, session: AsyncSession, client: Client
) -> None:
    ticket = await support.current_ticket(client.id)

    await support.record_message(
        ticket.id,
        MessageAuthor.CLIENT,
        MessageContent(text=f"Не работает {SUBSCRIPTION_URL}"),
        client_message_id=10,
        group_message_id=2001,
    )

    [saved] = await messages(session)
    assert saved.text is not None
    assert SUBSCRIPTION_CODE_HEAD not in saved.text
    assert saved.text.startswith("Не работает https://")


async def test_first_support_reply_sets_response_time_once(
    support: SupportService, session: AsyncSession, client: Client, clock: FakeClock
) -> None:
    ticket = await support.current_ticket(client.id)
    first_reply_at = clock.now + timedelta(minutes=15)
    clock.now = first_reply_at

    await support.record_message(
        ticket.id,
        MessageAuthor.NOTE,
        MessageContent(text="// смотрю"),
        client_message_id=None,
        group_message_id=2002,
    )
    assert (await stored_ticket(session, ticket.id)).first_response_at is None  # заметка — не ответ

    for group_message_id in (2003, 2004):
        await support.record_message(
            ticket.id,
            MessageAuthor.SUPPORT,
            MessageContent(text="Здравствуйте!"),
            client_message_id=20,
            group_message_id=group_message_id,
        )
        clock.advance(timedelta(minutes=5))

    assert (await stored_ticket(session, ticket.id)).first_response_at == first_reply_at


async def test_message_ids_map_between_client_chat_and_topic(
    support: SupportService, client: Client
) -> None:
    ticket = await support.current_ticket(client.id)
    await support.record_message(
        ticket.id,
        MessageAuthor.CLIENT,
        MessageContent(text="Вопрос"),
        client_message_id=10,
        group_message_id=2001,
    )
    await support.record_message(
        ticket.id,
        MessageAuthor.NOTE,
        MessageContent(text="// заметка"),
        client_message_id=None,
        group_message_id=2002,
    )

    assert await support.client_message_for(ticket.id, 2001) == 10
    assert await support.group_message_for(ticket.id, 10) == 2001
    assert await support.client_message_for(ticket.id, 2002) is None  # заметки у клиента нет
    assert await support.client_message_for(ticket.id, 9999) is None
    assert await support.group_message_for(ticket.id + 1, 10) is None  # только своё обращение


async def test_attach_topic_replaces_a_lost_topic(
    support: SupportService, session: AsyncSession, client: Client
) -> None:
    ticket = await support.current_ticket(client.id)
    await support.attach_topic(ticket.id, TOPIC_ID)

    await support.attach_topic(ticket.id, TOPIC_ID + 1)

    assert (await stored_ticket(session, ticket.id)).topic_id == TOPIC_ID + 1
    assert await support.ticket_by_topic(TOPIC_ID) is None
