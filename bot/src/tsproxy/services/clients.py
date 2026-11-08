"""Клиенты бота: запись при первом визите и пометка «заблокировал бота».

Сервис ничего не знает про Telegram: получает Telegram ID, имя и @username как обычные значения.
"""

import logging
from dataclasses import dataclass

from sqlalchemy import exists, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.db.models import NAME_LENGTH, USERNAME_LENGTH, Client, Order
from tsproxy.db.repository import affected_rows
from tsproxy.services.clock import Clock, utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClientProfile:
    """Что Telegram сообщает о клиенте при каждом обращении к боту."""

    telegram_id: int
    name: str | None
    username: str | None


def _trimmed(value: str | None, limit: int) -> str | None:
    """Обрезать строку до длины колонки: PostgreSQL не примет длиннее."""
    return None if value is None else value[:limit]


class ClientService:
    """Запись клиентов в базу. Каждый метод — своя короткая транзакция."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], clock: Clock) -> None:
        self._session_factory = session_factory
        self._clock = clock

    async def register(self, profile: ClientProfile) -> Client:
        """`/start`: создать клиента или обновить имя и @username, снять пометку «заблокировал».

        Клиент нажал `/start` — значит, бот снова может ему писать.
        """
        return await self._upsert(profile, clear_blocked=True)

    async def resolve(self, profile: ClientProfile) -> Client:
        """Клиент по Telegram ID; если его ещё нет в базе (не нажимал `/start`) — записать.

        Владельца данных определяет только Telegram ID нажавшего, а не данные кнопки (аудит §4.3).
        """
        async with self._session_factory() as session:
            client = await self._find(session, profile.telegram_id)
        if client is not None:
            return client
        return await self._upsert(profile, clear_blocked=False)

    async def find(self, telegram_id: int) -> Client | None:
        async with self._session_factory() as session:
            return await self._find(session, telegram_id)

    async def trial_offered(self, client: Client) -> bool:
        """Показывать ли «🎁 Попробовать бесплатно»: пробный не брал и ни разу не платил.

        Платящему кнопка не нужна (решение владельца 2026-10-25); если нажмёт старую
        кнопку — `TrialService` ответит «у вас уже есть подписка».
        """
        if client.trial_granted_at is not None:
            return False
        async with self._session_factory() as session:
            paid = await session.scalar(
                select(exists().where(Order.client_id == client.id, Order.paid_at.is_not(None)))
            )
        return not paid

    async def mark_blocked(self, telegram_id: int) -> bool:
        """Отметить «клиент заблокировал бота». `False` — клиента нет или отметка уже стоит."""
        async with self._session_factory() as session:
            result = await session.execute(
                update(Client)
                .where(Client.telegram_id == telegram_id, Client.bot_blocked_at.is_(None))
                .values(bot_blocked_at=utc_now(self._clock))
            )
            await session.commit()
        marked = affected_rows(result) == 1
        if marked:
            logger.info("Клиент %s заблокировал бота", telegram_id)
        return marked

    async def _find(self, session: AsyncSession, telegram_id: int) -> Client | None:
        return await session.scalar(select(Client).where(Client.telegram_id == telegram_id))

    async def _upsert(self, profile: ClientProfile, *, clear_blocked: bool) -> Client:
        name = _trimmed(profile.name, NAME_LENGTH)
        username = _trimmed(profile.username, USERNAME_LENGTH)
        async with self._session_factory() as session:
            client = await self._find(session, profile.telegram_id)
            if client is None:
                client = Client(
                    telegram_id=profile.telegram_id,
                    name=name,
                    username=username,
                    first_seen_at=utc_now(self._clock),
                )
                session.add(client)
                try:
                    await session.commit()
                except IntegrityError:
                    # Два апдейта одного клиента пришли одновременно: строку успел создать
                    # параллельный запрос (уникальный telegram_id) — обновляем её.
                    await session.rollback()
                    logger.info("Клиент %s уже записан параллельным запросом", profile.telegram_id)
                    client = await self._find(session, profile.telegram_id)
                    if client is None:
                        raise
                else:
                    logger.info("Новый клиент %s", profile.telegram_id)
                    return client
            client.name = name
            client.username = username
            if clear_blocked:
                client.bot_blocked_at = None
            await session.commit()
            return client
