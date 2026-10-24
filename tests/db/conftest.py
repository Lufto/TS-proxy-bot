from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tsproxy.db.engine import create_engine, create_session_factory
from tsproxy.db.models import Base, Client

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(sqlite_url(tmp_path))
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with create_session_factory(engine)() as session:
        yield session


@pytest.fixture
async def client(session: AsyncSession) -> Client:
    client = Client(telegram_id=111, name="Иван", first_seen_at=NOW)
    session.add(client)
    await session.commit()
    return client
