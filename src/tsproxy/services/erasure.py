"""Удаление данных клиента по запросу (задача 24, решение 8 задачи 21).

Что удаляется:
- имя и @username клиента (поля строки «Клиенты» стираются, сама строка остаётся);
- вся переписка поддержки (тексты и `file_id` файлов) и сами обращения;
- темы обращений в группе администраторов (через `TopicRemover`).

Что остаётся, обезличенным:
- строка клиента с Telegram ID, отметкой пробного и датой первого визита. Без неё тот же
  Telegram ID снова получил бы пробный (безопасный вариант — второго пробного нет), а Telegram
  ID нужен, чтобы находить прокси-аккаунт `tg_<ID>` в Remnawave;
- заказы, возвраты и журнал событий — бессрочно, это доказательство оказания услуги и оплат.
  Имени, @username и текстов переписки в них нет (подробности событий — только номера и суммы);
- прокси-аккаунт в Remnawave не трогаем: в боевом режиме обёртка не удаляет аккаунты, а
  отключать оплаченный доступ — отдельное решение администратора.

Чего удаление не достаёт: резервные копии базы (ежедневные живут две недели, месячные —
бессрочно), уже отправленные сообщения в теме «💰 Оплаты» с именем клиента, журнал
(лог) сервера. Это — до их ротации или вручную.

Порядок: сначала темы в Telegram, потом одна транзакция в базе. Не удалилась тема — в базе
ничего не меняется, повтор безопасен (уже удалённая тема — не ошибка, см. `TopicRemover`).
Сообщения клиента в поддержку на время удаления бот придерживает тем же замком, что и
поддержку (`SupportGuard.lock`). В базе удаляются только обращения, найденные в начале
удаления (их темы уже удалены): написанное клиентом после — новые данные, они остаются.
Клиент снова нажмёт `/start` — бот, как обычно, запишет его нынешние имя и @username.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.models import Client, Order, SupportTicket, TicketMessage
from tsproxy.db.repository import affected_rows
from tsproxy.services.events import EventKind, EventLog

logger = logging.getLogger(__name__)

# Действие администратора в подробностях события `ADMIN_ACTION`
ERASE_ACTION = "data_erased"


class TopicRemovalFailed(Exception):
    """Тему в группе удалить не вышло (нет права «Управление темами», сеть, …)."""


class TopicRemover(Protocol):
    """Удаляет тему обращения в группе администраторов.

    Уже удалённая (или не существующая) тема — не ошибка: повтор удаления безопасен.
    Остальные сбои — `TopicRemovalFailed`.
    """

    async def remove(self, topic_id: int) -> None: ...


class ErasureOutcome(StrEnum):
    ERASED = "erased"
    NOTHING_TO_ERASE = "nothing_to_erase"  # уже удалено (второе нажатие) или нечего удалять
    CLIENT_NOT_FOUND = "client_not_found"


@dataclass(frozen=True)
class ErasurePreview:
    """Что будет удалено (имя, @username, обращения, сообщения, темы) и что останется (заказы)."""

    telegram_id: int
    name: str | None
    username: str | None
    tickets: int
    messages: int
    topics: int
    orders: int


@dataclass(frozen=True)
class ErasureResult:
    """Итог удаления: сколько обращений и сообщений удалено и какие темы группы."""

    outcome: ErasureOutcome
    tickets: int = 0
    messages: int = 0
    topic_ids: tuple[int, ...] = ()

    @property
    def topics(self) -> int:
        return len(self.topic_ids)


@dataclass(frozen=True)
class _Ticket:
    id: int
    topic_id: int | None


class DataErasureService:
    """Предпросмотр и удаление данных клиента бота (по Telegram ID)."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        topics: TopicRemover,
        events: EventLog,
    ) -> None:
        self._session_factory = session_factory
        self._topics = topics
        self._events = events

    async def preview(self, telegram_id: int) -> ErasurePreview | None:
        """Что удалится. Ничего не меняет. `None` — не клиент бота."""
        async with self._session_factory() as session:
            client = await _client(session, telegram_id)
            if client is None:
                return None
            tickets = await _tickets(session, client.id)
            messages = await _message_count(session, [t.id for t in tickets])
            orders = await session.scalar(
                select(func.count()).select_from(Order).where(Order.client_id == client.id)
            )
        return ErasurePreview(
            telegram_id=telegram_id,
            name=client.name,
            username=client.username,
            tickets=len(tickets),
            messages=messages,
            topics=sum(1 for t in tickets if t.topic_id is not None),
            orders=orders or 0,
        )

    async def erase(self, admin_id: int, telegram_id: int) -> ErasureResult:
        """Удалить имя, @username, переписку, обращения и их темы — после подтверждения.

        Сбой удаления темы (`TopicRemovalFailed`) летит дальше, в базе ничего не меняется.
        """
        async with self._session_factory() as session:
            client = await _client(session, telegram_id)
            if client is None:
                return ErasureResult(ErasureOutcome.CLIENT_NOT_FOUND)
            tickets = await _tickets(session, client.id)
        if not tickets and client.name is None and client.username is None:
            return ErasureResult(ErasureOutcome.NOTHING_TO_ERASE)
        topic_ids = [t.topic_id for t in tickets if t.topic_id is not None]
        for topic_id in topic_ids:
            await self._topics.remove(topic_id)
        ticket_ids = [t.id for t in tickets]
        async with self._session_factory() as session:
            messages = await _delete_tickets(session, ticket_ids)
            await session.execute(
                update(Client).where(Client.id == client.id).values(name=None, username=None)
            )
            await self._events.record(
                session,
                EventKind.ADMIN_ACTION,
                client_id=client.id,
                admin_id=admin_id,
                details={
                    "action": ERASE_ACTION,
                    "tickets": len(ticket_ids),
                    "messages": messages,
                    "topics": len(topic_ids),
                },
            )
            await session.commit()
        logger.info(
            "Администратор %s удалил данные клиента %s: обращений %s, сообщений %s, тем %s",
            admin_id,
            client.id,
            len(ticket_ids),
            messages,
            len(topic_ids),
        )
        return ErasureResult(ErasureOutcome.ERASED, len(ticket_ids), messages, tuple(topic_ids))


async def _client(session: AsyncSession, telegram_id: int) -> Client | None:
    return await session.scalar(select(Client).where(Client.telegram_id == telegram_id))


async def _tickets(session: AsyncSession, client_id: int) -> list[_Ticket]:
    found = await session.execute(
        select(SupportTicket.id, SupportTicket.topic_id)
        .where(SupportTicket.client_id == client_id)
        .order_by(SupportTicket.id)
    )
    return [_Ticket(ticket_id, topic_id) for ticket_id, topic_id in found]


async def _message_count(session: AsyncSession, ticket_ids: list[int]) -> int:
    if not ticket_ids:
        return 0
    count = await session.scalar(
        select(func.count())
        .select_from(TicketMessage)
        .where(TicketMessage.ticket_id.in_(ticket_ids))
    )
    return count or 0


async def _delete_tickets(session: AsyncSession, ticket_ids: list[int]) -> int:
    """Удалить переписку и сами обращения. Возвращает, сколько сообщений удалено."""
    if not ticket_ids:
        return 0
    messages = await session.execute(
        delete(TicketMessage).where(TicketMessage.ticket_id.in_(ticket_ids))
    )
    await session.execute(delete(SupportTicket).where(SupportTicket.id.in_(ticket_ids)))
    return affected_rows(messages)
