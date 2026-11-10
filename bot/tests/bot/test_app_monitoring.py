"""Планировщик отмечает запуски для сторожа; задачи «раз в сутки»; сборка мониторинга."""

import asyncio
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import httpx
import pytest
from test_app import make_settings, prod_settings

from tsproxy.app import DailyJob, PeriodicJob, PeriodicJobs, build_application, close_application
from tsproxy.bot.fakes import FakeTelegramSession
from tsproxy.content.loader import Content
from tsproxy.integrations.fake_payments import FakePaymentProvider
from tsproxy.services.fakes import FakeClock
from tsproxy.services.job_health import JobHealth
from tsproxy.web.health import HEALTH_PATH

NOW = datetime(2026, 10, 25, 7, 0, tzinfo=UTC)  # 10:00 МСК


async def wait_for(condition: object, attempts: int = 200) -> None:
    for _ in range(attempts):
        if callable(condition) and condition():
            return
        await asyncio.sleep(0.005)


async def test_jobs_mark_success_and_failure() -> None:
    clock = FakeClock(NOW)
    health = JobHealth(clock)
    calls = {"ok": 0, "bad": 0}

    async def ok() -> None:
        calls["ok"] += 1

    async def bad() -> None:
        calls["bad"] += 1
        raise RuntimeError("всегда падает")

    jobs = PeriodicJobs(
        [
            PeriodicJob("ok", timedelta(hours=1), ok, run_at_start=True),
            PeriodicJob("bad", timedelta(hours=1), bad, run_at_start=True),
        ],
        health,
        clock,
    )
    jobs.start()
    await wait_for(lambda: calls["ok"] and calls["bad"])
    await jobs.stop()

    states = {state.name: state for state in health.states()}
    assert states["ok"].last_success == NOW
    assert states["bad"].last_success is None
    assert states["bad"].last_failure == NOW
    assert states["bad"].running_since is None


async def test_failed_job_is_reported_with_its_name() -> None:
    reported: list[tuple[str, str]] = []

    async def bad() -> None:
        raise RuntimeError("всегда падает")

    async def on_error(name: str, error: Exception) -> None:
        reported.append((name, str(error)))

    jobs = PeriodicJobs(
        [PeriodicJob("bad", timedelta(hours=1), bad, run_at_start=True)],
        on_error=on_error,
    )
    jobs.start()
    await wait_for(lambda: reported)
    await jobs.stop()

    assert reported == [("bad", "всегда падает")]


async def test_hung_job_stays_running_for_watchdog() -> None:
    clock = FakeClock(NOW)
    started = asyncio.Event()

    async def hang() -> None:
        started.set()
        await asyncio.sleep(3600)

    jobs = PeriodicJobs(
        [PeriodicJob("hang", timedelta(minutes=1), hang, run_at_start=True)], None, clock
    )
    jobs.start()
    await asyncio.wait_for(started.wait(), 1)
    clock.advance(timedelta(minutes=6))
    [stale] = jobs.health.stale()
    await jobs.stop()

    assert stale.name == "hang"
    assert stale.running_since == NOW


async def test_daily_job_runs_once_at_its_time() -> None:
    # 50 мс до 10:00 МСК
    clock = FakeClock(NOW - timedelta(milliseconds=50))
    runs: list[datetime] = []

    async def report() -> None:
        runs.append(clock.now)

    jobs = PeriodicJobs([DailyJob("daily_report", time(10, 0), report)], None, clock)
    jobs.start()
    await asyncio.sleep(0.02)
    assert runs == []  # ещё рано
    clock.now = NOW  # часы дошли до 10:00, sleep вот-вот проснётся
    await wait_for(lambda: runs)
    await asyncio.sleep(0.1)
    await jobs.stop()

    assert len(runs) == 1


def test_daily_job_is_stale_after_26_hours() -> None:
    clock = FakeClock(NOW)
    jobs = PeriodicJobs([DailyJob("daily_report", time(10, 0), _noop)], None, clock)
    clock.advance(timedelta(hours=26, minutes=1))
    assert [state.name for state in jobs.health.stale()] == ["daily_report"]


async def _noop() -> None:
    return None


async def test_dev_app_has_health_and_monitoring_jobs(
    tmp_path: Path, content: Content, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = build_application(
        make_settings(tmp_path),  # пути к бэкапам панели — по умолчанию, серверные
        content,
        telegram_session=FakeTelegramSession(),
    )

    async def panel_version() -> str:
        return "3.4.5"  # вместо настоящей панели

    monkeypatch.setattr(app.remnawave, "check_panel_version", panel_version)
    try:
        names = {state.name for state in app.jobs.health.states()}
        transport = httpx.ASGITransport(app=app.web)
        async with httpx.AsyncClient(transport=transport, base_url="http://bot.test") as client:
            response = await client.get(HEALTH_PATH)
    finally:
        await close_application(app)

    assert (response.status_code, response.text) == (200, "ok")
    assert {"watchdog", "daily_report"} <= names
    assert "bot_backup" not in names  # на Mac бэкап базы бота не запускается
    assert "panel_backup_check" not in names  # на Mac панели нет — присмотр не запускается


async def test_prod_app_backs_up_bot_database_and_watches_panel_backups(
    tmp_path: Path, content: Content
) -> None:
    app = build_application(
        prod_settings(tmp_path),
        content,
        telegram_session=FakeTelegramSession(),
        payments=FakePaymentProvider(lambda: datetime.now(UTC)),
    )
    try:
        names = {state.name for state in app.jobs.health.states()}
    finally:
        await close_application(app)

    assert {"watchdog", "daily_report", "bot_backup", "panel_backup_check"} <= names
