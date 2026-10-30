"""Проверка `/health`: база, Remnawave, фоновые задачи; результат помнится 30 секунд."""

import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from tsproxy.config import RunMode
from tsproxy.db.engine import create_engine, create_session_factory
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import RemnawaveUnavailable
from tsproxy.services.fakes import FakeClock
from tsproxy.services.health import HEALTH_CACHE_FOR, HealthCheck
from tsproxy.services.job_health import JobHealth


class CountingRemnawave(FakeRemnawave):
    """Считает обращения к панели — проверка не должна бить по ней на каждый запрос."""

    def __init__(self) -> None:
        super().__init__(RunMode.PROD)
        self.version_calls = 0

    async def check_panel_version(self) -> str:
        self.version_calls += 1
        await asyncio.sleep(0)
        return await super().check_panel_version()


class HangingRemnawave(FakeRemnawave):
    async def check_panel_version(self) -> str:
        await asyncio.sleep(3600)
        return self.version


@pytest.fixture
def session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(engine)


@pytest.fixture
def remnawave() -> CountingRemnawave:
    return CountingRemnawave()


@pytest.fixture
def jobs(clock: FakeClock) -> JobHealth:
    jobs = JobHealth(clock)
    jobs.watch("alerts_flush", timedelta(minutes=5))
    return jobs


def make_check(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: FakeRemnawave,
    jobs: JobHealth,
    clock: FakeClock,
) -> HealthCheck:
    return HealthCheck(session_factory, remnawave, jobs, clock)


async def test_all_good_is_healthy(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: CountingRemnawave,
    jobs: JobHealth,
    clock: FakeClock,
) -> None:
    assert await make_check(session_factory, remnawave, jobs, clock).is_healthy()


async def test_panel_down_is_a_problem(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: CountingRemnawave,
    jobs: JobHealth,
    clock: FakeClock,
) -> None:
    remnawave.fail_next(RemnawaveUnavailable("нет связи"))
    assert not await make_check(session_factory, remnawave, jobs, clock).is_healthy()


async def test_hanging_panel_times_out(
    session_factory: async_sessionmaker[AsyncSession], jobs: JobHealth, clock: FakeClock
) -> None:
    check = HealthCheck(
        session_factory,
        HangingRemnawave(RunMode.PROD),
        jobs,
        clock,
        part_timeout=timedelta(milliseconds=10),
    )
    assert not await check.is_healthy()


async def test_database_down_is_a_problem(
    tmp_path: Path, remnawave: CountingRemnawave, jobs: JobHealth, clock: FakeClock
) -> None:
    # вместо файла базы — папка: SQLite её не откроет
    broken = tmp_path / "broken.db"
    broken.mkdir()
    engine = create_engine(f"sqlite+aiosqlite:///{broken}")
    try:
        check = make_check(create_session_factory(engine), remnawave, jobs, clock)
        assert not await check.is_healthy()
    finally:
        await engine.dispose()


async def test_stale_job_is_a_problem(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: CountingRemnawave,
    jobs: JobHealth,
    clock: FakeClock,
) -> None:
    clock.advance(timedelta(minutes=6))
    assert not await make_check(session_factory, remnawave, jobs, clock).is_healthy()


async def test_result_is_cached_and_shared(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: CountingRemnawave,
    jobs: JobHealth,
    clock: FakeClock,
) -> None:
    """Поток запросов к /health — одно обращение к панели за 30 секунд."""
    check = make_check(session_factory, remnawave, jobs, clock)
    results = await asyncio.gather(*(check.is_healthy() for _ in range(20)))
    assert all(results)
    assert remnawave.version_calls == 1

    clock.advance(HEALTH_CACHE_FOR - timedelta(seconds=1))
    await check.is_healthy()
    assert remnawave.version_calls == 1

    clock.advance(timedelta(seconds=1))
    await check.is_healthy()
    assert remnawave.version_calls == 2


async def test_recovers_after_cache_expires(
    session_factory: async_sessionmaker[AsyncSession],
    remnawave: CountingRemnawave,
    jobs: JobHealth,
    clock: FakeClock,
) -> None:
    check = make_check(session_factory, remnawave, jobs, clock)
    remnawave.fail_next(RemnawaveUnavailable("нет связи"))
    assert not await check.is_healthy()
    clock.advance(HEALTH_CACHE_FOR)
    jobs.succeeded("alerts_flush")
    assert await check.is_healthy()
