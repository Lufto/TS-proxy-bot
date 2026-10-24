from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tsproxy.db.engine import create_engine, create_session_factory
from tsproxy.db.models import Base, Client
from tsproxy.logging_setup import MaskingFilter
from tsproxy.services.fakes import FakeClock
from tsproxy.services.masking import Masker

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
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


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def bot_token() -> str:
    return "8889627995:AAFsecretSECRETsecret"


@pytest.fixture
def mask(bot_token: str) -> Masker:
    return MaskingFilter([bot_token]).mask
