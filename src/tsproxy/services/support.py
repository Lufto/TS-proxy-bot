"""Обращения в поддержку и переписка (задача 16).

Сервис ничего не знает про Telegram: получает номера сообщений и содержимое как обычные
значения. Сами файлы не храним — только `file_id` Telegram (решение владельца).
Тексты переписки в лог не пишем; в базу — с замаскированными ссылками подписки и секретами
(предложение аудита §10: в тему уходит как есть, в базе — без полной ссылки).

Одновременные сообщения одного клиента сервис не упорядочивает — это делает бот
(`bot.support_guard.SupportGuard.lock`): иначе альбом из нескольких фото открыл бы
несколько обращений.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.enums import MessageAuthor, TicketStatus
from tsproxy.db.models import Client, SupportTicket, TicketMessage
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.masking import Masker


@dataclass(frozen=True)
class MessageContent:
    """Что сохранить из сообщения: текст или подпись, `file_id` и вид файла (`photo`, …)."""

    text: str | None
    file_id: str | None = None
    file_kind: str | None = None


@dataclass(frozen=True)
class TicketRef:
    """Обращение для бота: номер, чей клиент, тема в группе.

    `created` — обращение только что открыто этим сообщением (клиенту — подтверждение).
    """

    id: int
    client_telegram_id: int
    topic_id: int | None
    status: TicketStatus
    created: bool = False


class SupportService:
    """Обращения и переписка в базе. Каждый метод — своя короткая транзакция."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], clock: Clock, mask: Masker
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock
        self._mask = mask

    async def current_ticket(self, client_id: int) -> TicketRef:
        """Открытое обращение клиента; если его нет — открыть новое (без темы)."""
        async with self._session_factory() as session:
            found = await session.execute(
                self._ticket_query()
                .where(
                    SupportTicket.client_id == client_id,
                    SupportTicket.status == TicketStatus.OPEN,
                )
                .order_by(SupportTicket.opened_at.desc(), SupportTicket.id.desc())
                .limit(1)
            )
            row = found.first()
            if row is not None:
                return _ref(*row)
            ticket = SupportTicket(client_id=client_id, opened_at=utc_now(self._clock))
            session.add(ticket)
            await session.flush()
            telegram_id = await session.scalar(
                select(Client.telegram_id).where(Client.id == client_id)
            )
            if telegram_id is None:
                raise ValueError(f"Клиента {client_id} нет в базе")
            await session.commit()
            return _ref(ticket, telegram_id, created=True)

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


def _ref(ticket: SupportTicket, client_telegram_id: int, *, created: bool = False) -> TicketRef:
    return TicketRef(
        id=ticket.id,
        client_telegram_id=client_telegram_id,
        topic_id=ticket.topic_id,
        status=ticket.status,
        created=created,
    )
