"""Обращения в поддержку: открыть или продолжить, тема в группе, переписка в базе."""

from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tsproxy.db.engine import create_session_factory
from tsproxy.db.enums import MessageAuthor, TicketStatus
from tsproxy.db.models import Client, Event, SupportTicket, TicketMessage
from tsproxy.domain.support import REOPEN_WINDOW, REPLY_REMINDER_AFTER
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.fakes import FakeClock
from tsproxy.services.masking import Masker
from tsproxy.services.support import MessageContent, SupportService, TicketChange

TOPIC_ID = 501
SUBSCRIPTION_URL = "https://sub.ts-proxy.com/AbCdEfGh12345678"
SUBSCRIPTION_CODE_HEAD = "AbCdEfGh1234"
ADMIN_ID = 900
OTHER_ADMIN_ID = 901


@pytest.fixture
def support(engine: AsyncEngine, clock: FakeClock, mask: Masker) -> SupportService:
    return SupportService(create_session_factory(engine), clock, mask, EventLog(clock, mask))


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


# --- Задача 17: «🙋 Беру», «✅ Закрыть», переоткрытие, напоминание об ответе ---


async def events(session: AsyncSession) -> list[Event]:
    query = select(Event).order_by(Event.id)
    return list(await session.scalars(query, execution_options={"populate_existing": True}))


async def write(
    support: SupportService, ticket_id: int, author: MessageAuthor, text: str = "текст"
) -> None:
    await support.record_message(
        ticket_id, author, MessageContent(text=text), client_message_id=None, group_message_id=None
    )


async def test_close_marks_ticket_closed_and_logs_admin_action(
    support: SupportService, session: AsyncSession, client: Client, clock: FakeClock
) -> None:
    ticket = await support.current_ticket(client.id)

    assert await support.close(ticket.id, ADMIN_ID) is TicketChange.DONE

    saved = await stored_ticket(session, ticket.id)
    assert (saved.status, saved.closed_at) == (TicketStatus.CLOSED, clock.now)
    [event] = await events(session)
    assert (event.kind, event.admin_id, event.client_id) == (
        EventKind.ADMIN_ACTION.value,
        ADMIN_ID,
        client.id,
    )
    assert event.details == {"action": "support_close", "ticket_id": ticket.id}


async def test_closing_twice_changes_nothing(
    support: SupportService, session: AsyncSession, client: Client, clock: FakeClock
) -> None:
    ticket = await support.current_ticket(client.id)
    await support.close(ticket.id, ADMIN_ID)
    closed_at = clock.now
    clock.advance(timedelta(minutes=1))

    assert await support.close(ticket.id, OTHER_ADMIN_ID) is TicketChange.UNCHANGED

    assert (await stored_ticket(session, ticket.id)).closed_at == closed_at
    assert len(await events(session)) == 1


async def test_unknown_ticket_is_not_found(support: SupportService) -> None:
    assert await support.close(404, ADMIN_ID) is TicketChange.NOT_FOUND
    assert await support.take(404, ADMIN_ID) is TicketChange.NOT_FOUND
    assert await support.ticket(404) is None


async def test_take_assigns_admin_and_logs_it(
    support: SupportService, session: AsyncSession, client: Client
) -> None:
    ticket = await support.current_ticket(client.id)

    assert await support.take(ticket.id, ADMIN_ID) is TicketChange.DONE

    found = await support.ticket(ticket.id)
    assert found is not None and found.assignee_admin_id == ADMIN_ID
    [event] = await events(session)
    assert (event.kind, event.admin_id, event.client_id) == (
        EventKind.ADMIN_ACTION.value,
        ADMIN_ID,
        client.id,
    )
    assert event.details == {"action": "support_take", "ticket_id": ticket.id}


async def test_take_again_by_same_admin_changes_nothing(
    support: SupportService, session: AsyncSession, client: Client
) -> None:
    ticket = await support.current_ticket(client.id)
    await support.take(ticket.id, ADMIN_ID)

    assert await support.take(ticket.id, ADMIN_ID) is TicketChange.UNCHANGED
    assert len(await events(session)) == 1


async def test_other_admin_can_take_over(
    support: SupportService, session: AsyncSession, client: Client
) -> None:
    ticket = await support.current_ticket(client.id)
    await support.take(ticket.id, ADMIN_ID)

    assert await support.take(ticket.id, OTHER_ADMIN_ID) is TicketChange.DONE

    found = await support.ticket(ticket.id)
    assert found is not None and found.assignee_admin_id == OTHER_ADMIN_ID
    assert [e.admin_id for e in await events(session)] == [ADMIN_ID, OTHER_ADMIN_ID]


async def test_closed_ticket_cannot_be_taken(
    support: SupportService, session: AsyncSession, client: Client
) -> None:
    ticket = await support.current_ticket(client.id)
    await support.close(ticket.id, ADMIN_ID)

    assert await support.take(ticket.id, OTHER_ADMIN_ID) is TicketChange.CLOSED
    assert len(await events(session)) == 1  # только закрытие


async def test_message_23_hours_after_closing_reopens_the_ticket(
    support: SupportService, session: AsyncSession, client: Client, clock: FakeClock
) -> None:
    first = await support.current_ticket(client.id)
    await support.attach_topic(first.id, TOPIC_ID)
    await support.take(first.id, ADMIN_ID)
    await support.close(first.id, ADMIN_ID)
    clock.advance(timedelta(hours=23))

    again = await support.current_ticket(client.id)

    assert (again.id, again.topic_id, again.created, again.reopened) == (
        first.id,
        TOPIC_ID,
        False,
        True,
    )
    assert again.status is TicketStatus.OPEN
    assert again.assignee_admin_id == ADMIN_ID  # кто взял — остаётся
    assert (await stored_ticket(session, first.id)).status is TicketStatus.OPEN
    following = await support.current_ticket(client.id)
    assert (following.id, following.reopened) == (first.id, False)  # переоткрыто один раз


async def test_message_25_hours_after_closing_opens_a_new_ticket(
    support: SupportService, session: AsyncSession, client: Client, clock: FakeClock
) -> None:
    first = await support.current_ticket(client.id)
    await support.attach_topic(first.id, TOPIC_ID)
    await support.close(first.id, ADMIN_ID)
    clock.advance(timedelta(hours=25))

    again = await support.current_ticket(client.id)

    assert again.id != first.id
    assert (again.created, again.reopened, again.topic_id) == (True, False, None)
    assert (await stored_ticket(session, first.id)).status is TicketStatus.CLOSED


async def test_only_the_latest_closed_ticket_reopens(
    support: SupportService, client: Client, clock: FakeClock
) -> None:
    old = await support.current_ticket(client.id)
    await support.close(old.id, ADMIN_ID)
    clock.advance(REOPEN_WINDOW * 2)
    latest = await support.current_ticket(client.id)
    await support.close(latest.id, ADMIN_ID)
    clock.advance(timedelta(hours=1))

    again = await support.current_ticket(client.id)

    assert (again.id, again.reopened) == (latest.id, True)


# --- Кто ждёт ответа дольше часа ---


async def overdue_ids(support: SupportService) -> list[int]:
    return [waiting.ticket.id for waiting in await support.overdue_tickets()]


async def test_ticket_without_reply_for_more_than_an_hour_is_overdue(
    support: SupportService, client: Client, clock: FakeClock
) -> None:
    ticket = await support.current_ticket(client.id)
    await support.attach_topic(ticket.id, TOPIC_ID)
    asked_at = clock.now
    await write(support, ticket.id, MessageAuthor.CLIENT)
    clock.advance(timedelta(minutes=30))
    await write(support, ticket.id, MessageAuthor.CLIENT)
    await write(support, ticket.id, MessageAuthor.NOTE)  # заметка — не ответ

    clock.now = asked_at + REPLY_REMINDER_AFTER
    assert await overdue_ids(support) == []
    clock.advance(timedelta(minutes=1))
    [waiting] = await support.overdue_tickets()

    assert (waiting.ticket.id, waiting.ticket.topic_id) == (ticket.id, TOPIC_ID)
    assert waiting.since == asked_at


async def test_reply_resets_the_wait(
    support: SupportService, client: Client, clock: FakeClock
) -> None:
    ticket = await support.current_ticket(client.id)
    await support.attach_topic(ticket.id, TOPIC_ID)
    await write(support, ticket.id, MessageAuthor.CLIENT)
    clock.advance(timedelta(minutes=10))
    await write(support, ticket.id, MessageAuthor.SUPPORT)
    clock.advance(timedelta(hours=3))
    assert await overdue_ids(support) == []

    asked_again_at = clock.now
    await write(support, ticket.id, MessageAuthor.CLIENT)
    clock.advance(timedelta(hours=2))

    [waiting] = await support.overdue_tickets()
    assert waiting.since == asked_again_at


async def test_closed_tickets_and_tickets_without_topic_are_not_overdue(
    support: SupportService, session: AsyncSession, client: Client, clock: FakeClock
) -> None:
    other = Client(telegram_id=222, first_seen_at=clock.now)
    session.add(other)
    await session.commit()
    closed = await support.current_ticket(client.id)
    await support.attach_topic(closed.id, TOPIC_ID)
    await write(support, closed.id, MessageAuthor.CLIENT)
    await support.close(closed.id, ADMIN_ID)
    no_topic = await support.current_ticket(other.id)  # тема не создалась
    await write(support, no_topic.id, MessageAuthor.CLIENT)
    clock.advance(timedelta(hours=2))

    assert await overdue_ids(support) == []


async def test_after_reopening_client_waits_from_the_new_message(
    support: SupportService, client: Client, clock: FakeClock
) -> None:
    """Закрыли без ответа (клиент написал «спасибо») — старое сообщение уже разобрано."""
    ticket = await support.current_ticket(client.id)
    await support.attach_topic(ticket.id, TOPIC_ID)
    await write(support, ticket.id, MessageAuthor.CLIENT, "спасибо")
    await support.close(ticket.id, ADMIN_ID)
    clock.advance(timedelta(hours=5))
    reopened = await support.current_ticket(client.id)
    asked_at = clock.now
    await write(support, reopened.id, MessageAuthor.CLIENT, "снова не работает")

    clock.advance(timedelta(minutes=30))
    assert await overdue_ids(support) == []
    clock.advance(timedelta(minutes=31))
    [waiting] = await support.overdue_tickets()
    assert waiting.since == asked_at
