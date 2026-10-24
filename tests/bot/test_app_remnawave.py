"""Сборка приложения: вебхук Remnawave и сверка (задача 09) — без сети."""

import asyncio
from datetime import timedelta
from pathlib import Path

import httpx
from test_app import make_settings

from tsproxy.app import PeriodicJob, PeriodicJobs, build_application, close_application
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.web.remnawave import REMNAWAVE_WEBHOOK_PATH

SECRET = "Abc123" * 6


async def remnawave_webhook_status(
    settings_overrides: dict[str, object], tmp_path: Path, content: Content
) -> int:
    app = build_application(
        make_settings(tmp_path, **settings_overrides),
        content,
        telegram_session=FakeTelegramSession(),
    )
    try:
        transport = httpx.ASGITransport(app=app.web)
        async with httpx.AsyncClient(transport=transport, base_url="http://bot.test") as client:
            response = await client.post(REMNAWAVE_WEBHOOK_PATH, content=b"{}")
    finally:
        await close_application(app)
    return response.status_code


async def test_webhook_route_requires_signature(tmp_path: Path, content: Content) -> None:
    status = await remnawave_webhook_status({"remnawave_webhook_secret": SECRET}, tmp_path, content)
    assert status == httpx.codes.FORBIDDEN


async def test_no_webhook_route_without_secret(tmp_path: Path, content: Content) -> None:
    assert await remnawave_webhook_status({}, tmp_path, content) == httpx.codes.NOT_FOUND


async def test_reconciliation_job_runs_at_start(tmp_path: Path, content: Content) -> None:
    app = build_application(
        make_settings(tmp_path), content, telegram_session=FakeTelegramSession()
    )
    await close_application(app)
    [job] = [j for j in app.jobs._jobs if j.name == "remnawave_reconcile"]
    assert job.run_at_start
    assert timedelta(minutes=5) <= job.interval <= timedelta(minutes=10)


async def test_job_with_run_at_start_does_not_wait_for_interval() -> None:
    calls = 0

    async def job() -> None:
        nonlocal calls
        calls += 1

    jobs = PeriodicJobs([PeriodicJob("now", timedelta(hours=1), job, run_at_start=True)])
    jobs.start()
    for _ in range(100):
        if calls:
            break
        await asyncio.sleep(0.001)
    await jobs.stop()
    assert calls == 1
