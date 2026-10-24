"""Пробный период по кнопке «🎁 Попробовать бесплатно» (задача 07).

Один раз на Telegram ID. Гарантию «ровно один раз» даёт база: `claim_trial` — атомарный
`UPDATE … WHERE trial_granted_at IS NULL`, поэтому из двух одновременных нажатий
пробный получает только одно.

Порядок: сначала отметка в базе (короткая транзакция), потом аккаунт в Remnawave, потом
событие `TRIAL_GRANTED` в журнале. Событие и есть признак «пробный доставлен».
Держать транзакцию базы открытой на время запроса к панели нельзя — SQLite заблокирует
запись для всех остальных клиентов.

Панель не ответила (или бот упал между шагами) — отметка остаётся без события. Пробный
за клиентом: фоновая задача `deliver_pending` раз в минуту доделывает выдачу и бот сам
присылает ссылку (решение владельца 2026-10-25). Доделывать можно не раньше чем через
`STALE_CLAIM_AFTER`: до этого выдачу, возможно, ещё ведёт первое нажатие. Перехват
отметки тоже атомарный (`reclaim_trial`), и 7 дней считаются от момента, когда доступ
действительно появился.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.models import Client, Event, Order
from tsproxy.db.repository import claim_trial, reclaim_trial
from tsproxy.domain.constants import UNLIMITED_TRAFFIC_BYTES
from tsproxy.domain.trial import TRIAL_PERIOD, start_trial
from tsproxy.integrations.remnawave_types import (
    AccountNotFound,
    ProxyAccount,
    RemnawaveClient,
    RemnawaveUnavailable,
)
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind, EventLog

logger = logging.getLogger(__name__)

# Дольше, чем обёртка Remnawave ждёт ответа на чтение и создание аккаунта со всеми повторами
STALE_CLAIM_AFTER = timedelta(minutes=2)
# Зависшие выдачи старше этого фоновая задача не трогает: клиент может нажать кнопку сам
PENDING_TRIAL_WINDOW = TRIAL_PERIOD
PENDING_BATCH_SIZE = 50


class TrialOutcome(StrEnum):
    """Чем закончилась попытка выдать пробный."""

    GRANTED = "granted"  # пробный выдан сейчас
    ALREADY_USED = "already_used"  # пробный уже был доставлен
    HAS_SUBSCRIPTION = "has_subscription"  # клиент платил или у него уже есть подписка
    IN_PROGRESS = "in_progress"  # пробный выдаёт параллельное нажатие
    PENDING = "pending"  # панель не ответила: пробный за клиентом, выдаст фоновая задача


@dataclass(frozen=True)
class TrialResult:
    """`account` заполнен для GRANTED и, если известен, для HAS_SUBSCRIPTION."""

    outcome: TrialOutcome
    account: ProxyAccount | None = None


@dataclass(frozen=True)
class DeliveredTrial:
    """Пробный, который доделала фоновая задача: клиенту нужно прислать ссылку."""

    telegram_id: int
    account: ProxyAccount


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
        """Нажатие «Попробовать бесплатно». Сбой панели не бросает, а даёт PENDING."""
        async with self._session_factory() as session:
            client = await session.get_one(Client, client_id)
            claimed_at = client.trial_granted_at
            if await _has_paid_order(session, client_id):
                return TrialResult(TrialOutcome.HAS_SUBSCRIPTION)
            if claimed_at is not None and await _trial_delivered(session, client_id, claimed_at):
                return TrialResult(TrialOutcome.ALREADY_USED)
        try:
            account = await self._find_account(telegram_id)
        except RemnawaveUnavailable as error:
            logger.warning("Пробный клиента %s: панель не ответила: %s", client_id, error)
            return await self._claim_for_later(client_id, claimed_at)
        # Пока ждали панель, параллельное нажатие могло отметить пробный и создать аккаунт:
        # без перечитывания он выглядел бы как «подписка уже есть»
        async with self._session_factory() as session:
            claimed_at = await session.scalar(
                select(Client.trial_granted_at).where(Client.id == client_id)
            )
        if account is not None and claimed_at is None:
            return TrialResult(TrialOutcome.HAS_SUBSCRIPTION, account)
        return await self._deliver(client_id, telegram_id, claimed_at, account)

    async def deliver_pending(self) -> list[DeliveredTrial]:
        """Фоновая задача: доделать выдачи, которые не прошли из-за панели.

        Панель всё ещё не отвечает — проход заканчивается, следующий будет через минуту.
        """
        delivered: list[DeliveredTrial] = []
        for client_id, telegram_id, claimed_at in await self._pending_claims():
            try:
                account = await self._find_account(telegram_id)
            except RemnawaveUnavailable as error:
                logger.warning("Зависшие пробные: панель не ответила: %s", error)
                break
            result = await self._deliver(client_id, telegram_id, claimed_at, account)
            if result.outcome is TrialOutcome.PENDING:
                break
            if result.outcome is TrialOutcome.GRANTED and result.account is not None:
                delivered.append(DeliveredTrial(telegram_id, result.account))
        return delivered

    async def _find_account(self, telegram_id: int) -> ProxyAccount | None:
        try:
            return await self._remnawave.get_account(telegram_id)
        except AccountNotFound:
            return None  # нормальная ситуация: новый клиент

    async def _claim_for_later(self, client_id: int, claimed_at: datetime | None) -> TrialResult:
        """Панель не ответила ещё до выдачи: отметить пробный — выдаст фоновая задача."""
        if claimed_at is not None:
            return TrialResult(TrialOutcome.PENDING)
        async with self._session_factory() as session:
            if not await claim_trial(session, client_id, utc_now(self._clock)):
                return TrialResult(TrialOutcome.IN_PROGRESS)
            await session.commit()
        return TrialResult(TrialOutcome.PENDING)

    async def _deliver(
        self,
        client_id: int,
        telegram_id: int,
        claimed_at: datetime | None,
        account: ProxyAccount | None,
    ) -> TrialResult:
        """Отметить (или перехватить зависшую) выдачу и довести её до конца.

        `account` — аккаунт, если он уже есть: панель создала его, но не ответила.
        """
        if account is not None and account.traffic_limit_bytes == UNLIMITED_TRAFFIC_BYTES:
            # пока пробный висел, клиент оплатил: лимит пробного уже снят
            return TrialResult(TrialOutcome.HAS_SUBSCRIPTION, account)
        async with self._session_factory() as session:
            if not await self._claim(session, client_id, claimed_at):
                return TrialResult(TrialOutcome.IN_PROGRESS)
            await session.commit()
        try:
            account = await self._grant(client_id, telegram_id, account)
        except RemnawaveUnavailable as error:
            logger.warning("Пробный клиента %s: панель не ответила: %s", client_id, error)
            return TrialResult(TrialOutcome.PENDING)
        return TrialResult(TrialOutcome.GRANTED, account)

    async def _claim(
        self, session: AsyncSession, client_id: int, claimed_at: datetime | None
    ) -> bool:
        """Отметить выдачу или перехватить зависшую. `False` — выдаёт другое нажатие."""
        now = utc_now(self._clock)
        if claimed_at is None:
            return await claim_trial(session, client_id, now)
        if now - claimed_at < STALE_CLAIM_AFTER:
            return False
        logger.warning("Клиент %s: пробный отмечен, но не доставлен — доделываем", client_id)
        return await reclaim_trial(session, client_id, claimed_at, now)

    async def _grant(
        self, client_id: int, telegram_id: int, account: ProxyAccount | None
    ) -> ProxyAccount:
        """Создать прокси-аккаунт с лимитами пробного (если его нет) и записать событие."""
        if account is None:
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

    async def _pending_claims(self) -> list[tuple[int, int, datetime]]:
        """Отмеченные, но не доставленные пробные: (id клиента, Telegram ID, отметка)."""
        now = utc_now(self._clock)
        delivered = exists().where(
            Event.client_id == Client.id,
            Event.kind == EventKind.TRIAL_GRANTED.value,
            Event.occurred_at >= Client.trial_granted_at,
        )
        query = (
            select(Client.id, Client.telegram_id, Client.trial_granted_at)
            .where(
                and_(
                    Client.trial_granted_at <= now - STALE_CLAIM_AFTER,
                    Client.trial_granted_at > now - PENDING_TRIAL_WINDOW,
                ),
                ~delivered,
            )
            .order_by(Client.trial_granted_at)
            .limit(PENDING_BATCH_SIZE)
        )
        async with self._session_factory() as session:
            rows = await session.execute(query)
            return [(row[0], row[1], row[2]) for row in rows]


async def _has_paid_order(session: AsyncSession, client_id: int) -> bool:
    return bool(
        await session.scalar(
            select(exists().where(Order.client_id == client_id, Order.paid_at.is_not(None)))
        )
    )


async def _trial_delivered(session: AsyncSession, client_id: int, claimed_at: datetime) -> bool:
    """Пробный доставлен — после отметки записано событие «пробный выдан».

    Сравнение с отметкой нужно, чтобы сброс пробного администратором (задача 18) и новая
    отметка не считались доставленными по старому событию.
    """
    return bool(
        await session.scalar(
            select(
                exists().where(
                    Event.client_id == client_id,
                    Event.kind == EventKind.TRIAL_GRANTED.value,
                    Event.occurred_at >= claimed_at,
                )
            )
        )
    )
