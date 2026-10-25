"""Льготный период по окончании платной подписки (задача 11, аудит §7.5).

Обработчик проверок аккаунтов (`AccountCheckHandler`): его вызывают и вебхук Remnawave
(`user.expired`), и сверка раз в 5 минут, поэтому решение принимается по текущему
состоянию (`domain/grace.decide_on_expiry`), а все шаги повторяемы:

1. Платный срок кончился — атомарная отметка «льготный начался» (`claim_grace`) и событие
   `GRACE_STARTED` одной транзакцией, затем в Remnawave срок «до конца льготного»
   (3 дня + «последний день»). Повторный вебхук видит отметку и дней не добавляет.
2. Пока идёт льготный — раз в сутки одно сообщение с кнопкой «Продлить»
   (`domain/grace.grace_notice`); в «последний день» — большое предупреждение.
3. Льготный прошёл — событие `GRACE_ENDED`, доступ выключается (аккаунт не удаляется),
   одно финальное сообщение.

Каждое сообщение сначала записывается в таблицу уведомлений (уникально по клиенту, виду
и дню), потом отправляется; не ушло из-за сбоя Telegram — запись снимается, следующая
проверка повторит. Пробного это не касается: его окончание — задача 10.
"""

import logging
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.enums import NotificationKind
from tsproxy.db.models import Client, Event
from tsproxy.db.repository import claim_grace, claim_notification, release_notification
from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.constants import UNLIMITED_TRAFFIC_BYTES
from tsproxy.domain.grace import (
    GraceAction,
    GraceNotice,
    GraceStage,
    decide_on_expiry,
    grace_ends_at,
    grace_notice,
)
from tsproxy.integrations.remnawave_types import AccountStatus, ProxyAccount, RemnawaveClient
from tsproxy.services.account_checks import AccountCheck
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind, EventLog

logger = logging.getLogger(__name__)

# Панель хранит срок с точностью до миллисекунд: срок, отличающийся от конца льготного
# меньше чем на это, считаем тем же самым (иначе сверка переписывала бы его каждые 5 минут)
EXPIRY_TOLERANCE = timedelta(minutes=1)

STAGE_KINDS: dict[GraceStage, NotificationKind] = {
    GraceStage.STARTED: NotificationKind.GRACE,
    GraceStage.REMINDER: NotificationKind.GRACE_REMINDER,
    GraceStage.LAST_DAY: NotificationKind.GRACE_LAST_DAY,
    GraceStage.ENDED: NotificationKind.GRACE_ENDED,
}


class GraceMessenger(Protocol):
    """Отправка сообщений о льготном периоде клиенту (в боте — `bot/grace.py`)."""

    async def send_grace_notice(
        self, telegram_id: int, stage: GraceStage, access_until: datetime
    ) -> bool:
        """Прислать сообщение. `False` — клиент заблокировал бота, повторять не нужно."""
        ...


def client_snapshot(client: Client, account: ProxyAccount) -> ClientSnapshot:
    """Состояние клиента для правил сроков.

    Пробный — то же правило, что в профиле (`services/profile.py`): пробный выдавался,
    а лимит трафика ещё стоит. Оплата снимает лимит 7 ГБ (аудит §7.6).
    """
    limited = account.traffic_limit_bytes != UNLIMITED_TRAFFIC_BYTES
    return ClientSnapshot(
        expire_at=account.expire_at,
        on_trial=client.trial_granted_at is not None and limited,
        grace_started_at=client.grace_started_at,
        access_disabled=account.status is AccountStatus.DISABLED,
    )


class GraceHandler:
    """Льготный период: начать, напоминать раз в сутки, по окончании выключить доступ."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        events: EventLog,
        messenger: GraceMessenger,
        clock: Clock,
    ) -> None:
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._events = events
        self._messenger = messenger
        self._clock = clock

    async def handle(self, check: AccountCheck) -> None:
        async with self._session_factory() as session:
            client = await session.scalar(
                select(Client).where(Client.telegram_id == check.telegram_id)
            )
        if client is None:
            # Аккаунт бота есть, а клиента в базе нет: отметку ставить некуда
            logger.warning("Льготный: клиента %s нет в базе — пропускаем", check.telegram_id)
            return
        now = check.checked_at
        decision = decide_on_expiry(client_snapshot(client, check.account), now)
        match decision.action:
            case GraceAction.START_GRACE if decision.grace_started_at is not None:
                await self._start(client, check.account, decision.grace_started_at, now)
            case GraceAction.ALREADY_IN_GRACE if decision.grace_started_at is not None:
                await self._keep(client, check.account, decision.grace_started_at, now)
            case GraceAction.END_GRACE:
                await self._end(client)
            case GraceAction.ALREADY_ENDED:
                await self._after_end(client, now)
            case _:
                return  # срок идёт или кончился пробный — не наше дело

    async def _start(
        self, client: Client, account: ProxyAccount, started_at: datetime, now: datetime
    ) -> None:
        """Отметка «льготный начался» и событие — одной транзакцией, затем срок в панели."""
        async with self._session_factory() as session:
            if not await claim_grace(session, client.id, started_at):
                # Отметку поставила параллельная проверка — она и доведёт дело до конца
                logger.info("Клиент %s: льготный уже начат", client.telegram_id)
                return
            await self._events.record(
                session,
                EventKind.GRACE_STARTED,
                client_id=client.id,
                details={
                    "expired_at": account.expire_at.isoformat(),
                    "grace_ends_at": grace_ends_at(started_at).isoformat(),
                },
            )
            await session.commit()
        logger.info("Клиент %s: начался льготный период", client.telegram_id)
        client.grace_started_at = started_at
        await self._keep(client, account, started_at, now)

    async def _keep(
        self, client: Client, account: ProxyAccount, started_at: datetime, now: datetime
    ) -> None:
        """Идёт льготный: срок в панели — до его конца, сегодняшнее сообщение — один раз."""
        ends_at = grace_ends_at(started_at)
        if account.expire_at > ends_at + EXPIRY_TOLERANCE:
            # Срок продлили дальше льготного (оплата или администратор), а отметку ещё
            # не сняли: доступ есть, напоминать «продлите» было бы неправдой
            return
        if account.expire_at < ends_at - EXPIRY_TOLERANCE:
            # Сначала доступ в панели, потом сообщение «мы сохранили доступ до …»
            await self._remnawave.set_expiry(client.telegram_id, ends_at)
            logger.info("Клиент %s: срок до конца льготного выставлен", client.telegram_id)
        await self._notify(client, grace_notice(started_at, now), ends_at)

    async def _end(self, client: Client) -> None:
        """Льготный прошёл: событие (один раз), выключить доступ, финальное сообщение.

        Событие — до выключения: по нему повторная проверка узнаёт, что доступ выключили
        мы, а не администратор, и что финальное сообщение — наше.
        """
        started_at = _grace_started(client)
        async with self._session_factory() as session:
            if not await self._end_recorded(session, client.id, started_at):
                await self._events.record(
                    session,
                    EventKind.GRACE_ENDED,
                    client_id=client.id,
                    details={"grace_started_at": started_at.isoformat()},
                )
                await session.commit()
        # Только выключаем: аккаунт и ссылка подписки остаются — после оплаты всё заработает
        await self._remnawave.disable_account(client.telegram_id)
        logger.info("Клиент %s: льготный кончился, доступ выключен", client.telegram_id)
        ends_at = grace_ends_at(started_at)
        await self._notify(client, GraceNotice(GraceStage.ENDED, ends_at), ends_at)

    async def _after_end(self, client: Client, now: datetime) -> None:
        """Доступ уже выключен. Если выключили мы, а финальное сообщение не ушло — повторить."""
        started_at = client.grace_started_at
        if started_at is None or now < grace_ends_at(started_at):
            return  # выключен не по окончании льготного (например, администратором)
        async with self._session_factory() as session:
            ended_by_us = await self._end_recorded(session, client.id, started_at)
        if ended_by_us:
            ends_at = grace_ends_at(started_at)
            await self._notify(client, GraceNotice(GraceStage.ENDED, ends_at), ends_at)

    async def _end_recorded(
        self, session: AsyncSession, client_id: int, started_at: datetime
    ) -> bool:
        """Записано ли событие «льготный закончился» для льготного, начатого в `started_at`."""
        return bool(
            await session.scalar(
                select(
                    exists().where(
                        Event.client_id == client_id,
                        Event.kind == EventKind.GRACE_ENDED.value,
                        Event.occurred_at >= started_at,
                    )
                )
            )
        )

    async def _notify(self, client: Client, notice: GraceNotice, access_until: datetime) -> None:
        """Прислать сообщение, если оно ещё не отправлено. Клиенту, заблокировавшему бота, — нет."""
        if client.bot_blocked_at is not None:
            return
        kind = STAGE_KINDS[notice.stage]
        async with self._session_factory() as session:
            claimed = await claim_notification(
                session, client.id, kind, notice.due_at, utc_now(self._clock)
            )
            await session.commit()
        if not claimed:
            return
        try:
            delivered = await self._messenger.send_grace_notice(
                client.telegram_id, notice.stage, access_until
            )
        # Сообщение не ушло: снимаем запись, чтобы следующая проверка повторила, и
        # пробрасываем ошибку — `AccountChecks` запишет её в лог, сверка поднимет тревогу
        except Exception:
            async with self._session_factory() as session:
                await release_notification(session, client.id, kind, notice.due_at)
                await session.commit()
            raise
        if not delivered:
            logger.info("Клиент %s заблокировал бота: сообщение не ушло", client.telegram_id)


def _grace_started(client: Client) -> datetime:
    if client.grace_started_at is None:
        raise ValueError(f"У клиента {client.telegram_id} нет отметки «льготный начался»")
    return client.grace_started_at
