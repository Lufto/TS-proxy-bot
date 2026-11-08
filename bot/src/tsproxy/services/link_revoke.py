"""Клиент сам перевыпускает ссылку подписки (задача 31): не чаще раза в сутки.

Время прошлого перевыпуска берётся из журнала событий (`SUBSCRIPTION_REVOKED` этого
клиента), отдельного поля в базе нет. Перевыпуск администратором (`ADMIN_ACTION`) попытку
клиента не расходует: его делают по просьбе клиента.

Двойное «Да». Проверка суток и перевыпуск идут под замком клиента, событие фиксируется
до снятия замка. Второе нажатие ждёт первое и видит его событие — второго перевыпуска нет.
Бот работает одним процессом (аудит §7.1), поэтому замка в памяти хватает.

Ссылка подписки — секрет: в журнал и логи она не попадает.
"""

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.models import Client, Event
from tsproxy.domain.link_revoke import link_revoke_available_at
from tsproxy.integrations.remnawave_types import AccountNotFound, ProxyAccount, RemnawaveClient
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.locks import KeyedLocks

logger = logging.getLogger(__name__)

# Кто перевыпустил ссылку — поле `by` в подробностях события `SUBSCRIPTION_REVOKED`
REVOKED_BY_CLIENT = "client"


@dataclass(frozen=True)
class LinkRevoked:
    """Ссылка перевыпущена. `account` — с новой ссылкой подписки."""

    account: ProxyAccount


@dataclass(frozen=True)
class LinkRevokeTooSoon:
    """Прошлый перевыпуск был меньше суток назад. Снова можно с `available_at` (UTC)."""

    available_at: datetime


@dataclass(frozen=True)
class LinkRevokeNoAccount:
    """Прокси-аккаунта нет — перевыпускать нечего."""


LinkRevokeResult = LinkRevoked | LinkRevokeTooSoon | LinkRevokeNoAccount


class LinkRevokeService:
    """Перевыпуск ссылки подписки самим клиентом. Клиент — всегда нажавший."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        events: EventLog,
        clock: Clock,
        locks: KeyedLocks,
    ) -> None:
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._events = events
        self._clock = clock
        self._locks = locks

    async def available_at(self, client: Client) -> datetime | None:
        """Когда клиенту снова можно перевыпустить ссылку. `None` — можно сейчас."""
        async with self._session_factory() as session:
            last = await _last_revoked_at(session, client.id)
        return link_revoke_available_at(utc_now(self._clock), last)

    async def revoke(self, client: Client) -> LinkRevokeResult:
        """Перевыпустить ссылку: старая перестаёт работать на всех устройствах.

        Сбой панели (`RemnawaveUnavailable` и др.) не скрываем — попытка не расходуется.
        """
        async with self._locks.hold(client.id):
            available_at = await self.available_at(client)
            if available_at is not None:
                logger.info("Клиент %s: перевыпуск ссылки раньше суток — отказ", client.id)
                return LinkRevokeTooSoon(available_at)
            try:
                # Каждый вызов даёт новую ссылку; обёртка его не повторяет
                account = await self._remnawave.revoke_subscription(client.telegram_id)
            except AccountNotFound:
                return LinkRevokeNoAccount()  # нормальная ситуация: подписки ещё нет
            await self._record(client.id)
        logger.info("Клиент %s перевыпустил ссылку подписки", client.id)
        return LinkRevoked(account)

    async def _record(self, client_id: int) -> None:
        async with self._session_factory() as session:
            await self._events.record(
                session,
                EventKind.SUBSCRIPTION_REVOKED,
                client_id=client_id,
                details={"by": REVOKED_BY_CLIENT},
            )
            await session.commit()


async def _last_revoked_at(session: AsyncSession, client_id: int) -> datetime | None:
    """Когда клиент сам перевыпускал ссылку в последний раз (по журналу событий)."""
    return await session.scalar(
        select(Event.occurred_at)
        .where(
            Event.kind == EventKind.SUBSCRIPTION_REVOKED.value,
            Event.client_id == client_id,
            Event.admin_id.is_(None),  # только перевыпуски самого клиента
        )
        .order_by(Event.occurred_at.desc())
        .limit(1)
    )
