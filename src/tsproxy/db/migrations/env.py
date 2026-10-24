"""Запуск миграций Alembic (async).

Адрес базы: `sqlalchemy.url` из конфигурации Alembic (так делают тесты),
иначе `DATABASE_URL` из окружения или `.env`.
"""

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from alembic.autogenerate.api import AutogenContext
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from tsproxy.db.engine import DatabaseSettings, ensure_sqlite_dir
from tsproxy.db.models import Base
from tsproxy.db.types import UTCDateTime

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or DatabaseSettings().database_url


def render_item(type_: str, obj: Any, autogen_context: AutogenContext) -> str | bool:
    """Миграции не зависят от кода бота: `UTCDateTime` пишем как обычный DateTime с поясом."""
    if type_ == "type" and isinstance(obj, UTCDateTime):
        return "sa.DateTime(timezone=True)"
    return False


def configure(**kwargs: Any) -> None:
    context.configure(
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite не умеет ALTER TABLE — Alembic пересоздаёт таблицу
        render_item=render_item,
        compare_type=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """Режим `--sql`: печатает SQL вместо применения."""
    configure(url=database_url(), literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # Без PRAGMA foreign_keys: при пересоздании таблиц SQLite (batch) проверка ключей мешает.
    url = database_url()
    ensure_sqlite_dir(url)
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
