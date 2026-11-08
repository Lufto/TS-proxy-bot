"""Обращения в поддержку и переписка (задачи 16–17).

Сервис ничего не знает про Telegram: получает номера сообщений и содержимое как обычные
значения. Сами файлы не храним — только `file_id` Telegram (решение владельца).
Тексты переписки в лог не пишем; в базу — с замаскированными ссылками подписки и секретами
(предложение аудита §10: в тему уходит как есть, в базе — без полной ссылки).

Одновременные сообщения одного клиента сервис не упорядочивает — это делает бот
(`bot.support_guard.SupportGuard.lock`): иначе альбом из нескольких фото открыл бы
несколько обращений.

Задача 17. «🙋 Беру» и «✅ Закрыть» пишутся в журнал событий (действие администратора);
обычные ответы — нет, они и так в переписке. Клиент пишет в течение суток после закрытия —
обращение переоткрывается. `closed_at` при переоткрытии не стирается: это «когда закрыли
в последний раз» (открыто ли обращение — по `status`). По нему видно, что сообщения клиента
до закрытия уже разобраны, и напоминание об ответе считается от новых.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.enums import MessageAuthor, TicketStatus
from tsproxy.db.models import Client, SupportTicket, TicketMessage
from tsproxy.domain.support import reopens, reply_overdue, waiting_since
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.masking import Masker

# Действия администратора с обращением — в подробностях события журнала
TAKE_ACTION = "support_take"
CLOSE_ACTION = "support_close"


@dataclass(frozen=True)
class MessageContent:
    """Что сохранить из сообщения: текст или подпись, `file_id` и вид файла (`photo`, …)."""

    text: str | None
    file_id: str | None = None
    file_kind: str | None = None


@dataclass(frozen=True)
class TicketRef:
    """Обращение для бота: номер, чей клиент, тема в группе, кто взял.

    `created` — обращение только что открыто этим сообщением (клиенту — подтверждение).
    `reopened` — закрытое обращение переоткрыто этим сообщением (в теме — новая карточка).
    """

    id: int
    client_telegram_id: int
    topic_id: int | None
    status: TicketStatus
    created: bool = False
    reopened: bool = False
    assignee_admin_id: int | None = None


class TicketChange(StrEnum):
    """Чем кончилось «🙋 Беру» или «✅ Закрыть»."""

    DONE = "done"
    UNCHANGED = "unchanged"  # уже взято этим администратором или уже закрыто
    CLOSED = "closed"  # «Беру» на закрытом обращении
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class WaitingTicket:
    """Открытое обращение, в котором клиент ждёт ответа с `since`."""

    ticket: TicketRef
    since: datetime


class SupportService:
    """Обращения и переписка в базе. Каждый метод — своя короткая транзакция."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        mask: Masker,
        events: EventLog,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._mask = mask
        self._events = events

    async def current_ticket(self, client_id: int) -> TicketRef:
        """Обращение для нового сообщения клиента.

        Есть открытое — продолжаем. Последнее закрыто не больше суток назад — переоткрываем.
        Иначе — новое обращение (без темы).
        """
        now = utc_now(self._clock)
        async with self._session_factory() as session:
            found = await session.execute(
                self._ticket_query()
                .where(SupportTicket.client_id == client_id)
                .order_by(SupportTicket.opened_at.desc(), SupportTicket.id.desc())
                .limit(1)
            )
            row = found.first()
            if row is not None:
                ticket, telegram_id = row
                if ticket.status is TicketStatus.OPEN:
                    return _ref(ticket, telegram_id)
                if ticket.closed_at is not None and reopens(ticket.closed_at, now):
                    ticket.status = TicketStatus.OPEN
                    await session.commit()
                    return _ref(ticket, telegram_id, reopened=True)
            return await self._open(session, client_id, now)

    async def _open(self, session: AsyncSession, client_id: int, now: datetime) -> TicketRef:
        ticket = SupportTicket(client_id=client_id, opened_at=now)
        session.add(ticket)
        await session.flush()
        telegram_id = await session.scalar(select(Client.telegram_id).where(Client.id == client_id))
        if telegram_id is None:
            raise ValueError(f"Клиента {client_id} нет в базе")
        await session.commit()
        return _ref(ticket, telegram_id, created=True)

    async def ticket(self, ticket_id: int) -> TicketRef | None:
        """Обращение по номеру (из кнопки в теме)."""
        async with self._session_factory() as session:
            found = await session.execute(self._ticket_query().where(SupportTicket.id == ticket_id))
            row = found.first()
        return None if row is None else _ref(*row)

    async def take(self, ticket_id: int, admin_id: int) -> TicketChange:
        """«🙋 Беру»: обращение ведёт `admin_id`. Другой администратор может перехватить."""
        async with self._session_factory() as session:
            client_id = await session.scalar(
                update(SupportTicket)
                .where(
                    SupportTicket.id == ticket_id,
                    SupportTicket.status == TicketStatus.OPEN,
                    (SupportTicket.assignee_admin_id.is_(None))
                    | (SupportTicket.assignee_admin_id != admin_id),
                )
                .values(assignee_admin_id=admin_id)
                .returning(SupportTicket.client_id)
            )
            if client_id is None:
                return await self._why_unchanged(session, ticket_id, closed=TicketChange.CLOSED)
            await self._log(session, TAKE_ACTION, ticket_id, client_id, admin_id)
            await session.commit()
        return TicketChange.DONE

    async def close(self, ticket_id: int, admin_id: int) -> TicketChange:
        """«✅ Закрыть». Одним UPDATE: двойное нажатие не закроет и не запишет дважды."""
        now = utc_now(self._clock)
        async with self._session_factory() as session:
            client_id = await session.scalar(
                update(SupportTicket)
                .where(SupportTicket.id == ticket_id, SupportTicket.status == TicketStatus.OPEN)
                .values(status=TicketStatus.CLOSED, closed_at=now)
                .returning(SupportTicket.client_id)
            )
            if client_id is None:
                return await self._why_unchanged(session, ticket_id, closed=TicketChange.UNCHANGED)
            await self._log(session, CLOSE_ACTION, ticket_id, client_id, admin_id)
            await session.commit()
        return TicketChange.DONE

    @staticmethod
    async def _why_unchanged(
        session: AsyncSession, ticket_id: int, *, closed: TicketChange
    ) -> TicketChange:
        status = await session.scalar(
            select(SupportTicket.status).where(SupportTicket.id == ticket_id)
        )
        if status is None:
            return TicketChange.NOT_FOUND
        return closed if status is TicketStatus.CLOSED else TicketChange.UNCHANGED

    async def _log(
        self, session: AsyncSession, action: str, ticket_id: int, client_id: int, admin_id: int
    ) -> None:
        await self._events.record(
            session,
            EventKind.ADMIN_ACTION,
            client_id=client_id,
            admin_id=admin_id,
            details={"action": action, "ticket_id": ticket_id},
        )

    async def overdue_tickets(self) -> list[WaitingTicket]:
        """Открытые обращения с темой, где клиент ждёт ответа дольше часа."""
        now = utc_now(self._clock)
        async with self._session_factory() as session:
            found = await session.execute(
                self._ticket_query().where(
                    SupportTicket.status == TicketStatus.OPEN, SupportTicket.topic_id.is_not(None)
                )
            )
            tickets = {ticket.id: (ticket, telegram_id) for ticket, telegram_id in found}
            if not tickets:
                return []
            exchange = await session.execute(
                select(TicketMessage.ticket_id, TicketMessage.author, TicketMessage.created_at)
                .where(
                    TicketMessage.ticket_id.in_(tickets),
                    TicketMessage.author.in_([MessageAuthor.CLIENT, MessageAuthor.SUPPORT]),
                )
                .order_by(TicketMessage.id)
            )
        client_times: dict[int, list[datetime]] = defaultdict(list)
        last_reply: dict[int, datetime] = {}
        for ticket_id, author, created_at in exchange:
            if author is MessageAuthor.CLIENT:
                client_times[ticket_id].append(created_at)
            else:
                last_reply[ticket_id] = max(created_at, last_reply.get(ticket_id, created_at))
        overdue = []
        for ticket_id, (ticket, telegram_id) in tickets.items():
            handled = [t for t in (last_reply.get(ticket_id), ticket.closed_at) if t is not None]
            since = waiting_since(client_times[ticket_id], max(handled, default=None))
            if since is not None and reply_overdue(since, now):
                overdue.append(WaitingTicket(_ref(ticket, telegram_id), since))
        return overdue

    async def attach_topic(self, ticket_id: int, topic_id: int) -> None:
        """Записать тему обращения в группе (новую или взамен удалённой)."""
        async with self._session_factory() as session:
            await session.execute(
                update(SupportTicket).where(SupportTicket.id == ticket_id).values(topic_id=topic_id)
            )
            await session.commit()

    async def ticket_by_topic(self, topic_id: int) -> TicketRef | None:
        """Обращение по теме группы. `None` — тема не обращения («🚨 Тревоги» и т. п.)."""
        async with self._session_factory() as session:
            found = await session.execute(
                self._ticket_query().where(SupportTicket.topic_id == topic_id)
            )
            row = found.first()
        return None if row is None else _ref(*row)

    async def record_message(
        self,
        ticket_id: int,
        author: MessageAuthor,
        content: MessageContent,
        *,
        client_message_id: int | None,
        group_message_id: int | None,
    ) -> None:
        """Сохранить сообщение переписки. Первый ответ поддержки отмечает время ответа."""
        now = utc_now(self._clock)
        async with self._session_factory() as session:
            session.add(
                TicketMessage(
                    ticket_id=ticket_id,
                    author=author,
                    text=None if content.text is None else self._mask(content.text),
                    file_id=content.file_id,
                    file_kind=content.file_kind,
                    client_message_id=client_message_id,
                    group_message_id=group_message_id,
                    created_at=now,
                )
            )
            if author is MessageAuthor.SUPPORT:
                await self._mark_first_response(session, ticket_id, now)
            await session.commit()

    async def client_message_for(self, ticket_id: int, group_message_id: int) -> int | None:
        """Номер сообщения у клиента, копия которого — `group_message_id` в теме."""
        async with self._session_factory() as session:
            return await session.scalar(
                select(TicketMessage.client_message_id)
                .where(
                    TicketMessage.ticket_id == ticket_id,
                    TicketMessage.group_message_id == group_message_id,
                    TicketMessage.client_message_id.is_not(None),
                )
                .limit(1)
            )

    async def group_message_for(self, ticket_id: int, client_message_id: int) -> int | None:
        """Номер сообщения в теме, соответствующего `client_message_id` в чате клиента."""
        async with self._session_factory() as session:
            return await session.scalar(
                select(TicketMessage.group_message_id)
                .where(
                    TicketMessage.ticket_id == ticket_id,
                    TicketMessage.client_message_id == client_message_id,
                    TicketMessage.group_message_id.is_not(None),
                )
                .limit(1)
            )

    @staticmethod
    def _ticket_query() -> Select[tuple[SupportTicket, int]]:
        """Обращение вместе с Telegram ID клиента."""
        return select(SupportTicket, Client.telegram_id).join(
            Client, Client.id == SupportTicket.client_id
        )

    @staticmethod
    async def _mark_first_response(session: AsyncSession, ticket_id: int, now: datetime) -> None:
        # одним UPDATE: время первого ответа не перезапишет второй, даже одновременный, ответ
        await session.execute(
            update(SupportTicket)
            .where(SupportTicket.id == ticket_id, SupportTicket.first_response_at.is_(None))
            .values(first_response_at=now)
        )


def _ref(
    ticket: SupportTicket,
    client_telegram_id: int,
    *,
    created: bool = False,
    reopened: bool = False,
) -> TicketRef:
    return TicketRef(
        id=ticket.id,
        client_telegram_id=client_telegram_id,
        topic_id=ticket.topic_id,
        status=ticket.status,
        created=created,
        reopened=reopened,
        assignee_admin_id=ticket.assignee_admin_id,
    )
