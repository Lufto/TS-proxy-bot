"""Карточка клиента в начале темы обращения (задача 17): подписка, устройства, оплаты, пробный.

Подписку и устройства знает только Remnawave, оплаты и пробный — база бота. Карточка —
подсказка администратору, поэтому сбой панели её не ломает: подписка в ней помечается
«панель не ответила», а сообщение клиента всё равно доходит до темы.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.models import Client, Order
from tsproxy.integrations.remnawave_types import RemnawaveError
from tsproxy.services.profile import Profile, ProfileService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PaymentsSummary:
    """Оплаченные заказы клиента: сколько, на какую сумму, когда последняя."""

    count: int
    total: Decimal
    last_paid_at: datetime | None


@dataclass(frozen=True)
class ClientCard:
    """Что знаем о клиенте. `profile` — `None`, если подписки нет или панель не ответила
    (`panel_failed`)."""

    telegram_id: int
    name: str | None
    username: str | None
    trial_granted_at: datetime | None
    payments: PaymentsSummary
    profile: Profile | None
    panel_failed: bool


class ClientCardService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], profiles: ProfileService
    ) -> None:
        self._session_factory = session_factory
        self._profiles = profiles

    async def card(self, telegram_id: int) -> ClientCard | None:
        """Карточка клиента. `None` — такого клиента в базе нет."""
        async with self._session_factory() as session:
            client = await session.scalar(select(Client).where(Client.telegram_id == telegram_id))
            if client is None:
                return None
            payments = await self._payments(session, client.id)
        profile, panel_failed = await self._profile(client)
        return ClientCard(
            telegram_id=client.telegram_id,
            name=client.name,
            username=client.username,
            trial_granted_at=client.trial_granted_at,
            payments=payments,
            profile=profile,
            panel_failed=panel_failed,
        )

    async def _profile(self, client: Client) -> tuple[Profile | None, bool]:
        try:
            return await self._profiles.profile(client), False
        except RemnawaveError as error:
            # любая ошибка панели: карточка выйдет без подписки, причина — в логе
            logger.warning(
                "Карточка клиента %s: панель не ответила (%s)",
                client.telegram_id,
                type(error).__name__,
            )
            return None, True

    @staticmethod
    async def _payments(session: AsyncSession, client_id: int) -> PaymentsSummary:
        found = await session.execute(
            select(
                func.count(Order.id), func.sum(Order.paid_amount), func.max(Order.paid_at)
            ).where(Order.client_id == client_id, Order.paid_at.is_not(None))
        )
        count, total, last_paid_at = found.one()
        return PaymentsSummary(
            count=count,
            total=Decimal(0) if total is None else Decimal(total),
            last_paid_at=last_paid_at,
        )
