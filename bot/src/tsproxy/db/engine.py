"""Подключение к базе: движок и фабрика сессий (async).

Переход на PostgreSQL — только сменой `DATABASE_URL`.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import ConnectionPoolEntry

from tsproxy.config import Settings

# Сколько ждать, если базу SQLite сейчас пишет другой запрос, прежде чем сдаться
SQLITE_BUSY_TIMEOUT_MS = 5000


class DatabaseSettings(BaseSettings):
    """Только адрес базы — чтобы миграции работали без токенов бота в `.env`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Settings.model_fields["database_url"].default


def _is_sqlite(url: str) -> bool:
    return make_url(url).get_backend_name() == "sqlite"


def ensure_sqlite_dir(url: str) -> None:
    """Создаёт папку для файла SQLite (`data/`), если её ещё нет."""
    if not _is_sqlite(url):
        return
    database = make_url(url).database
    if database and database != ":memory:":
        Path(database).parent.mkdir(parents=True, exist_ok=True)


def _set_sqlite_pragmas(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
    """SQLite по умолчанию не проверяет внешние ключи и плохо переносит параллельную запись."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.close()


def create_engine(url: str) -> AsyncEngine:
    """Движок базы. Для SQLite включает внешние ключи, WAL и ожидание блокировки."""
    ensure_sqlite_dir(url)
    engine = create_async_engine(url)
    if _is_sqlite(url):
        event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Фабрика сессий. Объекты не «протухают» после commit — их можно читать дальше."""
    return async_sessionmaker(engine, expire_on_commit=False)
