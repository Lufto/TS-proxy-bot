"""Выключатель пробных периодов (задача 28, решение владельца 2026-10-25 вместо лимитов в час).

- Администратор выключает и включает пробные командой `/trials`. Каждое действие — событие
  в журнале (`TRIALS_DISABLED`/`TRIALS_ENABLED` и `ADMIN_ACTION`) и сообщение в «🚨 Тревоги».
- Больше `TRIAL_SPAM_LIMIT` новых пробных за `TRIAL_SPAM_WINDOW` — бот выключает пробные сам
  и шлёт тревогу. Сам обратно не включает — только администратор.

Состояние без новой таблицы: источник правды — последнее событие «пробные выключены/включены»
в журнале. Бот читает его при первом обращении после запуска и дальше держит в памяти.

Счётчик новых пробных — в памяти: после перезапуска окно начинается заново. Если наплыв
продолжается, порог наберётся снова за те же 10 минут — перезапуск лишь чуть откладывает
автовыключение. Включение администратором тоже начинает счёт заново, иначе пробные
выключились бы сразу же по старому наплыву.
"""

import asyncio
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.content.loader import Texts
from tsproxy.db.models import Event
from tsproxy.domain.constants import TRIAL_SPAM_WINDOW
from tsproxy.domain.trial import recent_trials, trial_spam_detected
from tsproxy.services.alerts import Alerter
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.events import EventKind, EventLog

logger = logging.getLogger(__name__)

SECONDS_PER_MINUTE = 60
SWITCH_KINDS = (EventKind.TRIALS_DISABLED.value, EventKind.TRIALS_ENABLED.value)
SPAM_REASON = "spam"


class TrialSwitch:
    """Включены ли пробные. Меняет состояние только через журнал событий."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        events: EventLog,
        alerter: Alerter,
        admin_texts: Texts,
        clock: Clock,
    ) -> None:
        self._session_factory = session_factory
        self._events = events
        self._alerter = alerter
        self._admin_texts = admin_texts
        self._clock = clock
        self._enabled: bool | None = None  # None — ещё не прочитано из журнала
        self._recent: list[datetime] = []
        # Два администратора (или администратор и автовыключение) сразу — одно событие
        self._lock = asyncio.Lock()

    async def enabled(self) -> bool:
        """Включены ли пробные: из памяти, при первом обращении — из журнала."""
        if self._enabled is None:
            state = await self._read_state()
            # Пока читали, состояние мог поменять администратор — его решение новее
            if self._enabled is None:
                self._enabled = state
        return self._enabled

    async def disable(self, admin_id: int) -> bool:
        """Администратор выключает пробные. `False` — они уже были выключены."""
        async with self._lock:
            if not await self.enabled():
                return False
            event = await self._switch(EventKind.TRIALS_DISABLED, admin_id=admin_id)
        logger.warning("Пробные выключены администратором %s", admin_id)
        await self._alerter.raise_alert(
            f"trials_disabled_{event.id}",
            self._admin_texts.get("alerts.trials_disabled", admin_id=admin_id),
        )
        return True

    async def enable(self, admin_id: int) -> bool:
        """Администратор включает пробные. `False` — они уже были включены."""
        async with self._lock:
            if await self.enabled():
                return False
            event = await self._switch(EventKind.TRIALS_ENABLED, admin_id=admin_id)
            self._recent = []
        logger.warning("Пробные включены администратором %s", admin_id)
        await self._alerter.raise_alert(
            f"trials_enabled_{event.id}",
            self._admin_texts.get("alerts.trials_enabled", admin_id=admin_id),
        )
        return True

    async def note_new_trial(self) -> None:
        """Клиент только что взял пробный. Наплыв больше порога — выключить пробные."""
        now = utc_now(self._clock)
        self._recent = recent_trials([*self._recent, now], now)
        count = len(self._recent)
        if not trial_spam_detected(count):
            return
        async with self._lock:
            if not await self.enabled():
                return  # уже выключены: тревога была
            event = await self._switch(
                EventKind.TRIALS_DISABLED, details={"reason": SPAM_REASON, "count": count}
            )
        minutes = int(TRIAL_SPAM_WINDOW.total_seconds()) // SECONDS_PER_MINUTE
        logger.warning("Похоже на спам: %s пробных за %s мин — пробные выключены", count, minutes)
        await self._alerter.raise_alert(
            f"trials_spam_{event.id}",
            self._admin_texts.get("alerts.trials_spam", count=count, minutes=minutes),
        )

    async def _switch(
        self,
        kind: EventKind,
        *,
        admin_id: int | None = None,
        details: dict[str, object] | None = None,
    ) -> Event:
        """Записать новое состояние в журнал и запомнить его. Вызывать под `_lock`.

        Действие администратора — ещё и `ADMIN_ACTION`, как все его действия (задача 18).
        """
        async with self._session_factory() as session:
            event = await self._events.record(session, kind, admin_id=admin_id, details=details)
            if admin_id is not None:
                await self._events.record(
                    session,
                    EventKind.ADMIN_ACTION,
                    admin_id=admin_id,
                    details={"action": kind.value},
                )
            await session.commit()
        self._enabled = kind is EventKind.TRIALS_ENABLED
        return event

    async def _read_state(self) -> bool:
        """Последнее событие «выключены/включены»; событий нет — пробные включены."""
        async with self._session_factory() as session:
            last = await session.scalar(
                select(Event.kind)
                .where(Event.kind.in_(SWITCH_KINDS))
                .order_by(Event.occurred_at.desc(), Event.id.desc())
                .limit(1)
            )
        return last != EventKind.TRIALS_DISABLED.value
