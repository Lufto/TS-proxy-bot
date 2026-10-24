"""Связь нод (серверов прокси) с панелью: тревога о потере и о восстановлении (задача 09).

Состояние нод всегда перечитывается из API (`list_nodes`): вебхуки `node.connection_lost`
и `node.connection_restored` — только сигнал «проверь сейчас», а сверка раз в 5 минут
делает ту же проверку на случай, если вебхук потерялся (аудит §7.3).

Каждый сбой — строка в таблице `node_outages` (открыта, пока `ended_at` пуст). По ней
бот не поднимает повторную тревогу ни при повторном вебхуке, ни после перезапуска.
Кнопку «Компенсировать», склейку «мигания» и начисление делает задача 20 — по этим же
строкам (`domain/compensation.py`).

Выключенные администратором ноды (`is_disabled`) — не сбой: их не открываем и не закрываем.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.content.loader import Texts
from tsproxy.db.models import NodeOutage
from tsproxy.db.repository import affected_rows
from tsproxy.domain.dates import format_moscow
from tsproxy.integrations.remnawave_types import Node, RemnawaveClient
from tsproxy.services.alerts import Alerter
from tsproxy.services.clock import Clock, utc_now

logger = logging.getLogger(__name__)

ONE_MINUTE = timedelta(minutes=1)


def outage_started_at(node: Node, now: datetime) -> datetime:
    """Когда нода потеряла связь: время смены статуса из панели, если оно правдоподобно."""
    changed = node.last_status_change
    return changed if changed is not None and changed <= now else now


def outage_ended_at(node: Node, started_at: datetime, now: datetime) -> datetime:
    """Когда связь вернулась: время смены статуса, если оно между началом сбоя и «сейчас»."""
    changed = node.last_status_change
    return changed if changed is not None and started_at <= changed <= now else now


def outage_minutes(started_at: datetime, ended_at: datetime) -> int:
    """Длительность простоя в минутах, с округлением вверх."""
    return -(-(ended_at - started_at) // ONE_MINUTE)


class NodeMonitor:
    """Сверяет связь нод с записями о сбоях и поднимает тревоги при переходах."""

    def __init__(
        self,
        remnawave: RemnawaveClient,
        session_factory: async_sessionmaker[AsyncSession],
        alerter: Alerter,
        admin_texts: Texts,
        clock: Clock,
    ) -> None:
        self._remnawave = remnawave
        self._session_factory = session_factory
        self._alerter = alerter
        self._texts = admin_texts
        self._clock = clock
        # вебхук и сверка не должны одновременно открыть или закрыть один сбой
        self._lock = asyncio.Lock()

    async def refresh(self) -> None:
        """Перечитать ноды из панели и записать начавшиеся и закончившиеся сбои.

        Ошибки панели пробрасываются вызывающему (сверка поднимет тревогу).
        """
        async with self._lock:
            nodes = await self._remnawave.list_nodes()
            for node in nodes:
                if node.is_disabled:
                    continue
                if node.is_connected:
                    await self._close_outage(node)
                else:
                    await self._open_outage(node)

    async def _find_open(self, session: AsyncSession, node_uuid: str) -> NodeOutage | None:
        return await session.scalar(
            select(NodeOutage)
            .where(NodeOutage.node_uuid == node_uuid, NodeOutage.ended_at.is_(None))
            .order_by(NodeOutage.started_at.desc())
            .limit(1)
        )

    async def _open_outage(self, node: Node) -> None:
        now = utc_now(self._clock)
        started_at = outage_started_at(node, now)
        async with self._session_factory() as session:
            if await self._find_open(session, node.uuid) is not None:
                return  # сбой уже записан — тревога уже была
            session.add(NodeOutage(node_uuid=node.uuid, started_at=started_at))
            try:
                await session.commit()
            except IntegrityError:
                # этот же сбой (нода и время начала) уже записан и закрыт — второй раз не открываем
                await session.rollback()
                logger.info("Сбой ноды %s с %s уже записан", node.uuid, started_at.isoformat())
                return
        logger.warning("Нода %s потеряла связь с панелью", node.uuid)
        await self._alerter.raise_alert(
            f"node_down:{node.uuid}",
            self._texts.get("alerts.node_down", name=node.name, since=format_moscow(started_at)),
        )

    async def _close_outage(self, node: Node) -> None:
        now = utc_now(self._clock)
        async with self._session_factory() as session:
            outage = await self._find_open(session, node.uuid)
            if outage is None:
                return
            started_at = outage.started_at
            ended_at = outage_ended_at(node, started_at, now)
            result = await session.execute(
                update(NodeOutage)
                .where(NodeOutage.id == outage.id, NodeOutage.ended_at.is_(None))
                .values(ended_at=ended_at)
            )
            await session.commit()
        if affected_rows(result) != 1:
            return  # закрыл параллельный процесс
        logger.info("Нода %s: связь восстановлена", node.uuid)
        await self._alerter.raise_alert(
            f"node_restored:{node.uuid}",
            self._texts.get(
                "alerts.node_restored",
                name=node.name,
                minutes=outage_minutes(started_at, ended_at),
                since=format_moscow(started_at),
                until=format_moscow(ended_at),
            ),
        )
