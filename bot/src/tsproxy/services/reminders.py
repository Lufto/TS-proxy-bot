"""Напоминания, продления пробного и сообщения о пробном трафике (задача 10).

Обработчик проверок аккаунта (`AccountCheckHandler`, задача 09): его вызывают и вебхук
Remnawave, и сверка раз в 5 минут. Решает по текущему состоянию аккаунта и базы:
- пробный кончился, клиент не подключался — продлить на 3 дня (до 5 раз) и написать;
  продления кончились — одно прощальное сообщение (`domain/trial.py`);
- за день и за час до окончания пробного или оплаченного срока — напоминание
  (`domain/reminders.py`); льготный период — задача 11, здесь его не трогаем;
- пробный трафик: 80% — предупреждение, кончился — сообщение с кнопкой «Тарифы».

«Ровно один раз» даёт таблица уведомлений: запись (клиент, вид, срок) ставится **до**
отправки, уникальный ключ пропускает только одну. Упала отправка — напоминание
потеряется, но не придёт дважды. Клиенту, заблокировавшему бота, не пишем и отметку
не ставим: вернётся через `/start`, пока срок не прошёл, — напоминание придёт.

Продление пробного. Сначала в одной транзакции: счётчик продлений +1 (атомарно),
запись «пробный продлён» к старому сроку, событие в журнале. Потом срок в панели,
потом сообщение. Панель не ответила — запись есть, а срок в панели старый: следующая
проверка видит это и доделывает продление, не засчитывая его второй раз.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.enums import NotificationKind
from tsproxy.db.models import Client, Notification
from tsproxy.db.repository import claim_notification, claim_trial_extension
from tsproxy.domain.client_state import ClientSnapshot
from tsproxy.domain.reminders import (
    Reminder,
    ReminderKind,
    TrialTrafficNotice,
    due_reminder,
    trial_traffic_notice,
)
from tsproxy.domain.trial import (
    TrialEnd,
    TrialExpiryAction,
    decide_on_trial_expiry,
    trial_extension_end,
)
from tsproxy.integrations.remnawave_types import AccountStatus, ProxyAccount, RemnawaveClient
from tsproxy.services.account_checks import AccountCheck
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind, EventLog
from tsproxy.services.locks import KeyedLocks
from tsproxy.services.profile import on_trial

logger = logging.getLogger(__name__)

REMINDER_NOTIFICATIONS: dict[ReminderKind, NotificationKind] = {
    ReminderKind.DAY_BEFORE: NotificationKind.DAY_BEFORE,
    ReminderKind.HOUR_BEFORE: NotificationKind.HOUR_BEFORE,
}
TRAFFIC_NOTIFICATIONS: dict[TrialTrafficNotice, NotificationKind] = {
    TrialTrafficNotice.WARNING: NotificationKind.TRIAL_TRAFFIC_WARNING,
    TrialTrafficNotice.USED_UP: NotificationKind.TRIAL_TRAFFIC_USED_UP,
}


# --- Что сказать клиенту (тексты и кнопки собирает бот) ---


@dataclass(frozen=True)
class ExpiryReminder:
    """Срок кончается через день или через час."""

    reminder: Reminder


@dataclass(frozen=True)
class TrialExtended:
    """Пробный продлён, потому что клиент не подключался. `expire_at` — новый срок."""

    expire_at: datetime


@dataclass(frozen=True)
class TrialFarewell:
    """Продления пробного кончились — прощание."""


@dataclass(frozen=True)
class TrialTraffic:
    """Пробный трафик почти кончился или кончился."""

    notice: TrialTrafficNotice
    used_bytes: int
    limit_bytes: int


type ClientNotice = ExpiryReminder | TrialExtended | TrialFarewell | TrialTraffic


class ClientNotifier(Protocol):
    """Отправка уведомления клиенту в Telegram (`bot/notices.py`)."""

    async def notify(self, telegram_id: int, notice: ClientNotice) -> None: ...


# --- Обработчик ---


class ReminderService:
    """Обработчик проверок аккаунта: напоминания, продления пробного, пробный трафик."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        remnawave: RemnawaveClient,
        events: EventLog,
        notifier: ClientNotifier,
        clock: Clock,
    ) -> None:
        self._session_factory = session_factory
        self._remnawave = remnawave
        self._events = events
        self._notifier = notifier
        self._clock = clock
        # окончание пробного — по одному на клиента: вебхук и сверка приходят одновременно
        self._trial_locks = KeyedLocks()

    async def handle(self, check: AccountCheck) -> None:
        client = await self._find_client(check.telegram_id)
        if client is None:
            logger.warning("Аккаунт клиента %s есть в панели, а в базе нет", check.telegram_id)
            return
        account = check.account
        if account.status is AccountStatus.DISABLED or client.grace_started_at is not None:
            # доступ выключен (администратором или после льготного) — не напоминаем;
            # льготным периодом занимается задача 11
            return
        now = utc_now(self._clock)
        trial = on_trial(client, account)
        if trial and now >= account.expire_at:
            async with self._trial_locks.hold(client.id):
                await self._trial_expired(client, account, now)
            return
        snapshot = ClientSnapshot(
            expire_at=account.expire_at,
            on_trial=trial,
            grace_started_at=None,
            access_disabled=False,
        )
        if trial:
            await self._trial_traffic(client, account, snapshot, now)
        await self._reminder(client, account, snapshot, now)

    async def _find_client(self, telegram_id: int) -> Client | None:
        async with self._session_factory() as session:
            return await session.scalar(select(Client).where(Client.telegram_id == telegram_id))

    # --- Напоминания и трафик ---

    async def _reminder(
        self, client: Client, account: ProxyAccount, snapshot: ClientSnapshot, now: datetime
    ) -> None:
        reminder = due_reminder(
            snapshot,
            now,
            ever_connected=account.ever_connected,
            traffic_used_up=account.status is AccountStatus.LIMITED,
        )
        if reminder is None:
            return
        kind = REMINDER_NOTIFICATIONS[reminder.kind]
        await self._send_once(client, kind, reminder.ends_at, ExpiryReminder(reminder), now)

    async def _trial_traffic(
        self, client: Client, account: ProxyAccount, snapshot: ClientSnapshot, now: datetime
    ) -> None:
        notice = trial_traffic_notice(
            snapshot,
            now,
            used_bytes=account.traffic_used_bytes,
            limit_bytes=account.traffic_limit_bytes,
            traffic_used_up=account.status is AccountStatus.LIMITED,
        )
        if notice is None:
            return
        message = TrialTraffic(notice, account.traffic_used_bytes, account.traffic_limit_bytes)
        kind = TRAFFIC_NOTIFICATIONS[notice]
        await self._send_once(client, kind, account.expire_at, message, now)

    async def _send_once(
        self,
        client: Client,
        kind: NotificationKind,
        related_expire_at: datetime,
        notice: ClientNotice,
        now: datetime,
    ) -> None:
        """Отметить и отправить. Уже отмечено или клиент заблокировал бота — ничего."""
        if client.bot_blocked_at is not None:
            return
        async with self._session_factory() as session:
            if not await claim_notification(session, client.id, kind, related_expire_at, now):
                return
            await session.commit()
        logger.info("Клиент %s: уведомление %s", client.telegram_id, kind.value)
        await self._notifier.notify(client.telegram_id, notice)

    # --- Окончание пробного ---

    async def _trial_expired(self, client: Client, account: ProxyAccount, now: datetime) -> None:
        """Пробный кончился: продлить, попрощаться или ничего (подключался — без продлений).

        Доступ выключает сама панель по сроку; аккаунт не трогаем — после оплаты
        ссылка подписки останется прежней.
        """
        expire_at = account.expire_at
        if await self._extension_claimed(client.id, expire_at):
            current = await self._remnawave.get_account(client.telegram_id)
            if current.expire_at != expire_at:
                # срок в панели уже другой: продление дошло (параллельная проверка) или
                # клиент оплатил — доделывать нечего
                return
            logger.warning(
                "Клиент %s: продление пробного не дошло до панели — доделываем", client.id
            )
            await self._finish_extension(client, expire_at, now)
            return
        decision = decide_on_trial_expiry(
            TrialEnd(
                expire_at=expire_at,
                ever_connected=account.ever_connected,
                traffic_limit_reached=account.status is AccountStatus.LIMITED,
                extensions_used=client.trial_extensions,
            ),
            now,
        )
        if decision.action is TrialExpiryAction.EXTEND:
            if await self._claim_extension(client.id, expire_at, decision.extensions_used, now):
                await self._finish_extension(client, expire_at, now)
        elif decision.action is TrialExpiryAction.FAREWELL:
            await self._send_once(
                client, NotificationKind.FAREWELL, expire_at, TrialFarewell(), now
            )

    async def _extension_claimed(self, client_id: int, expire_at: datetime) -> bool:
        """Продление к этому сроку уже засчитано (а срок в панели остался прежним)."""
        async with self._session_factory() as session:
            return bool(
                await session.scalar(
                    select(
                        exists().where(
                            Notification.client_id == client_id,
                            Notification.kind == NotificationKind.TRIAL_EXTENDED,
                            Notification.related_expire_at == expire_at,
                        )
                    )
                )
            )

    async def _claim_extension(
        self, client_id: int, expire_at: datetime, extension: int, now: datetime
    ) -> bool:
        """Засчитать продление номер `extension` одной транзакцией. `False` — уже засчитано."""
        async with self._session_factory() as session:
            if not await claim_trial_extension(session, client_id, extension - 1):
                return False
            if not await claim_notification(
                session, client_id, NotificationKind.TRIAL_EXTENDED, expire_at, now
            ):
                return False
            await self._events.record(
                session,
                EventKind.TRIAL_EXTENDED,
                client_id=client_id,
                details={"previous_expire_at": expire_at.isoformat(), "extension": extension},
            )
            await session.commit()
        return True

    async def _finish_extension(self, client: Client, expire_at: datetime, now: datetime) -> None:
        """Поставить новый срок в панели и написать клиенту. Ошибки панели — наверх."""
        account = await self._remnawave.set_expiry(
            client.telegram_id, trial_extension_end(expire_at, now)
        )
        logger.info("Клиент %s: пробный продлён до %s", client.id, account.expire_at.isoformat())
        if client.bot_blocked_at is None:
            await self._notifier.notify(client.telegram_id, TrialExtended(account.expire_at))
