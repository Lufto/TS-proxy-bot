"""Плашка о неполадках на сайте, которую ставит администратор (задачи сайта 13–15).

Администратор ставит и снимает её кнопками (`/banner` и кнопка в тревоге о Remnawave).
Сама плашка не снимается (решение владельца 2026-11-09: «только кнопкой»).

Состояние без новой таблицы, как у выключателя пробных: источник правды — последнее событие
`SITE_BANNER_SET` в журнале. Бот читает его при первом обращении и дальше держит в памяти,
поэтому плашка переживает перезапуск. Каждое изменение — ещё и `ADMIN_ACTION` и сообщение
в «🚨 Тревоги», чтобы другие администраторы знали.
"""

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.content.loader import Texts
from tsproxy.db.models import Event
from tsproxy.domain.site_banner import SiteBanner
from tsproxy.services.alerts import Alerter
from tsproxy.services.events import EventKind, EventLog

logger = logging.getLogger(__name__)

BANNER_DETAIL = "banner"


class SiteBannerSwitch:
    """Какая плашка стоит на сайте. Меняет состояние только через журнал событий."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        events: EventLog,
        alerter: Alerter,
        admin_texts: Texts,
    ) -> None:
        self._session_factory = session_factory
        self._events = events
        self._alerter = alerter
        self._admin_texts = admin_texts
        self._current: SiteBanner | None = None  # None — ещё не прочитано из журнала
        # Два администратора сразу (кнопка в тревоге и /banner) — одно событие
        self._lock = asyncio.Lock()

    async def current(self) -> SiteBanner:
        """Плашка сейчас: из памяти, при первом обращении — из журнала."""
        if self._current is None:
            state = await self._read_state()
            # Пока читали, плашку мог поставить администратор — его решение новее
            if self._current is None:
                self._current = state
        return self._current

    async def set(self, banner: SiteBanner, admin_id: int) -> bool:
        """Поставить плашку (`NONE` — снять). `False` — она уже такая."""
        async with self._lock:
            if await self.current() is banner:
                return False
            async with self._session_factory() as session:
                event = await self._events.record(
                    session,
                    EventKind.SITE_BANNER_SET,
                    admin_id=admin_id,
                    details={BANNER_DETAIL: banner.value},
                )
                await self._events.record(
                    session,
                    EventKind.ADMIN_ACTION,
                    admin_id=admin_id,
                    details={
                        "action": EventKind.SITE_BANNER_SET.value,
                        BANNER_DETAIL: banner.value,
                    },
                )
                await session.commit()
            self._current = banner
        logger.warning("Плашка на сайте: %s (администратор %s)", banner.value, admin_id)
        await self._alerter.raise_alert(
            f"site_banner_{event.id}",
            self._admin_texts.get(
                "alerts.site_banner_set",
                banner=self._admin_texts.get(f"admin.site_banner.names.{banner.value}"),
                admin_id=admin_id,
            ),
        )
        return True

    async def _read_state(self) -> SiteBanner:
        """Последнее событие `SITE_BANNER_SET`; событий нет или значение незнакомое — плашки нет."""
        async with self._session_factory() as session:
            details = await session.scalar(
                select(Event.details)
                .where(Event.kind == EventKind.SITE_BANNER_SET.value)
                .order_by(Event.occurred_at.desc(), Event.id.desc())
                .limit(1)
            )
        value = (details or {}).get(BANNER_DETAIL)
        try:
            return SiteBanner(value)
        except ValueError:
            if value is not None:
                logger.error("В журнале незнакомая плашка %r — считаем, что её нет", value)
            return SiteBanner.NONE
