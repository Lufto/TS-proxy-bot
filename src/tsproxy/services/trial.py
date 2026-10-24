"""Пробный период по кнопке «🎁 Попробовать бесплатно» (задача 07).

Один раз на Telegram ID. Гарантию «ровно один раз» даёт база: `claim_trial` — атомарный
`UPDATE … WHERE trial_granted_at IS NULL`, поэтому из двух одновременных нажатий
пробный получает только одно.

Порядок: сначала отметка в базе (короткая транзакция), потом создание аккаунта в Remnawave.
Если панель не ответила на создании (или бот упал между шагами), отметка остаётся без
аккаунта. Такую выдачу доделывает следующее нажатие, но не раньше чем через
`STALE_CLAIM_AFTER`: до этого аккаунт, возможно, ещё создаёт первое нажатие. Перехват
тоже атомарный (`reclaim_trial`). Аккаунта нет — значит, доступа клиент ещё не получил,
и 7 дней считаются заново.
Держать транзакцию базы открытой на время запроса к панели нельзя — SQLite заблокирует
запись для всех остальных клиентов.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.models import Client
from tsproxy.db.repository import claim_trial, reclaim_trial
from tsproxy.domain.trial import start_trial
from tsproxy.integrations.remnawave_types import AccountNotFound, ProxyAccount, RemnawaveClient
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind, EventLog

logger = logging.getLogger(__name__)

# Дольше, чем обёртка Remnawave ждёт ответа на чтение и создание аккаунта со всеми повторами
STALE_CLAIM_AFTER = timedelta(minutes=2)


class TrialOutcome(StrEnum):
    """Чем закончилось нажатие «Попробовать бесплатно»."""

    GRANTED = "granted"  # пробный выдан сейчас
    ALREADY_USED = "already_used"  # пробный уже был (или его выдаёт параллельное нажатие)
    HAS_SUBSCRIPTION = "has_subscription"  # у клиента уже есть аккаунт — пробный не нужен


@dataclass(frozen=True)
class TrialResult:
    """`account` пуст, только если пробный выдаёт параллельное нажатие."""

    outcome: TrialOutcome
    account: ProxyAccount | None


class TrialService:
    """Выдача пробного периода: отметка в базе, аккаунт в Remnawave, событие в журнале."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        events: EventLog,
        clock: Clock,
    ) -> None:
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._events = events
        self._clock = clock

    async def start(self, client_id: int, telegram_id: int) -> TrialResult:
        """Выдать пробный период клиенту. Ошибки панели пробрасываются вызывающему коду."""
        account = await self._find_account(telegram_id)
        async with self._session_factory() as session:
            client = await session.get_one(Client, client_id)
            claimed_at = client.trial_granted_at
            if account is not None:
                outcome = (
                    TrialOutcome.HAS_SUBSCRIPTION
                    if claimed_at is None
                    else TrialOutcome.ALREADY_USED
                )
                return TrialResult(outcome, account)
            if not await self._claim(session, client_id, claimed_at):
                return TrialResult(TrialOutcome.ALREADY_USED, None)
            await session.commit()
        return TrialResult(TrialOutcome.GRANTED, await self._grant(client_id, telegram_id))

    async def _claim(
        self, session: AsyncSession, client_id: int, claimed_at: datetime | None
    ) -> bool:
        """Отметить выдачу или перехватить зависшую. `False` — выдаёт другое нажатие."""
        now = utc_now(self._clock)
        if claimed_at is None:
            return await claim_trial(session, client_id, now)
        if now - claimed_at < STALE_CLAIM_AFTER:
            return False
        logger.warning("Клиент %s: пробный отмечен, аккаунта нет — доделываем выдачу", client_id)
        return await reclaim_trial(session, client_id, claimed_at, now)

    async def _find_account(self, telegram_id: int) -> ProxyAccount | None:
        try:
            return await self._remnawave.get_account(telegram_id)
        except AccountNotFound:
            return None  # нормальная ситуация: новый клиент

    async def _grant(self, client_id: int, telegram_id: int) -> ProxyAccount:
        """Создать прокси-аккаунт с лимитами пробного и записать событие."""
        grant = start_trial(utc_now(self._clock))
        account = await self._remnawave.get_or_create_account(
            telegram_id,
            expire_at=grant.expire_at,
            device_limit=grant.device_limit,
            traffic_limit_bytes=grant.traffic_limit_bytes,
        )
        async with self._session_factory() as session:
            await self._events.record(
                session,
                EventKind.TRIAL_GRANTED,
                client_id=client_id,
                details={"expire_at": account.expire_at.isoformat()},
            )
            await session.commit()
        logger.info("Клиент %s: пробный период выдан", client_id)
        return account
