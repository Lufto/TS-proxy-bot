"""Доказательство оказания услуги по заказу — для споров с платёжкой (задача 18
в `vpn-bot-rework`, задача 22).

Собирает всё, что бот знает о заказе: сам заказ и оплату, клиента, журнал событий
(события заказа и все события клиента с момента создания заказа: выдача доступа,
продления, компенсации, действия администраторов), а из Remnawave — прокси-аккаунт
(первое подключение, трафик) и подключённые устройства. Ничего не меняет.

Панель не ответила — доказательство всё равно собирается из базы, с пометкой.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.models import Client, Event, Order
from tsproxy.integrations.remnawave_types import (
    AccountNotFound,
    Device,
    ProxyAccount,
    RemnawaveClient,
    RemnawaveUnavailable,
)
from tsproxy.services.clock import Clock, utc_now

logger = logging.getLogger(__name__)

# Больше событий в одну выгрузку не берём: у старого клиента их могут быть тысячи
PROOF_EVENTS_LIMIT = 500


@dataclass(frozen=True)
class ProofEvent:
    occurred_at: datetime
    kind: str
    admin_id: int | None
    details: dict[str, Any] | None
    repeat_count: int


@dataclass(frozen=True)
class PanelFacts:
    """Что сказала Remnawave. `available=False` — панель не ответила."""

    available: bool
    account: ProxyAccount | None = None
    devices: tuple[Device, ...] = ()


@dataclass(frozen=True)
class ServiceProof:
    collected_at: datetime
    order: Order
    client: Client
    events: tuple[ProofEvent, ...]
    truncated: bool  # событий больше `PROOF_EVENTS_LIMIT`, показаны первые
    panel: PanelFacts


class ServiceProofService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        clock: Clock,
    ) -> None:
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._clock = clock

    async def collect(self, order_id: int) -> ServiceProof | None:
        """Доказательство по заказу; `None` — такого заказа нет."""
        async with self._session_factory() as session:
            order = await session.get(Order, order_id)
            if order is None:
                return None
            client = await session.get(Client, order.client_id)
            if client is None:
                raise LookupError(f"У заказа #{order_id} нет клиента {order.client_id}")
            rows = list(
                await session.scalars(
                    select(Event)
                    .where(
                        or_(
                            Event.order_id == order.id,
                            (Event.client_id == client.id)
                            & (Event.occurred_at >= order.created_at),
                        )
                    )
                    .order_by(Event.occurred_at, Event.id)
                    .limit(PROOF_EVENTS_LIMIT + 1)
                )
            )
        events = tuple(
            ProofEvent(e.occurred_at, e.kind, e.admin_id, e.details, e.repeat_count)
            for e in rows[:PROOF_EVENTS_LIMIT]
        )
        return ServiceProof(
            collected_at=utc_now(self._clock),
            order=order,
            client=client,
            events=events,
            truncated=len(rows) > PROOF_EVENTS_LIMIT,
            panel=await self._panel(client.telegram_id),
        )

    async def _panel(self, telegram_id: int) -> PanelFacts:
        try:
            account = await self._remnawave.get_account(telegram_id)
        except AccountNotFound:
            return PanelFacts(available=True)  # аккаунта нет — это тоже факт
        except RemnawaveUnavailable as error:
            logger.warning("Доказательство услуги: панель не ответила: %s", error)
            return PanelFacts(available=False)
        try:
            devices = await self._remnawave.list_devices(telegram_id)
        except RemnawaveUnavailable as error:
            logger.warning("Доказательство услуги: панель не отдала устройства: %s", error)
            return PanelFacts(available=False, account=account)
        return PanelFacts(available=True, account=account, devices=tuple(devices))
