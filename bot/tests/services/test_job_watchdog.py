"""Сторож фоновых задач: замечает зависшую и падающую задачу, тревога — раз на эпизод."""

from datetime import timedelta

import pytest
from signal_helpers import admin_texts, make_alerter

from tsproxy.services.fakes import FakeAlertSender, FakeClock
from tsproxy.services.job_health import JobHealth, UnknownJobError
from tsproxy.services.masking import Masker
from tsproxy.services.watchdog import JobWatchdog

LIMIT = timedelta(minutes=5)


@pytest.fixture
def health(clock: FakeClock) -> JobHealth:
    health = JobHealth(clock)
    health.watch("remnawave_reconcile", LIMIT)
    return health


@pytest.fixture
def sender() -> FakeAlertSender:
    return FakeAlertSender()


@pytest.fixture
def watchdog(
    health: JobHealth, sender: FakeAlertSender, clock: FakeClock, mask: Masker
) -> JobWatchdog:
    return JobWatchdog(health, make_alerter(sender, clock, mask), admin_texts())


async def test_healthy_job_raises_nothing(
    health: JobHealth, watchdog: JobWatchdog, sender: FakeAlertSender, clock: FakeClock
) -> None:
    for _ in range(5):
        clock.advance(timedelta(minutes=4))
        health.started("remnawave_reconcile")
        health.succeeded("remnawave_reconcile")
        await watchdog.run()
    assert sender.sent == []


async def test_hung_job_is_noticed(
    health: JobHealth, watchdog: JobWatchdog, sender: FakeAlertSender, clock: FakeClock
) -> None:
    """Задача начала работу и не вернулась — сторож говорит «похоже, зависла»."""
    health.started("remnawave_reconcile")
    health.succeeded("remnawave_reconcile")
    clock.advance(timedelta(minutes=1))
    health.started("remnawave_reconcile")  # и больше не вернулась
    clock.advance(LIMIT)
    await watchdog.run()

    [alert] = sender.sent
    assert "remnawave_reconcile" in alert.text
    assert "зависла" in alert.text


async def test_failing_job_is_noticed(
    health: JobHealth, watchdog: JobWatchdog, sender: FakeAlertSender, clock: FakeClock
) -> None:
    for _ in range(4):
        clock.advance(timedelta(minutes=2))
        health.started("remnawave_reconcile")
        health.failed("remnawave_reconcile")
    await watchdog.run()

    [alert] = sender.sent
    texts = admin_texts()
    assert texts.get("alert.job_stale_never.title", name="remnawave_reconcile") in alert.text
    assert "ни разу не отработала" in alert.text
    assert texts.get("alert.job_stale.hint_failing") in alert.text
    assert "JobWatchdog.run" in alert.text
    assert texts.get("alert.job_stale_never.causes.1.cause") in alert.text


async def test_stale_job_alerts_once_per_episode(
    health: JobHealth, watchdog: JobWatchdog, sender: FakeAlertSender, clock: FakeClock
) -> None:
    clock.advance(LIMIT + timedelta(minutes=1))
    for _ in range(30):
        await watchdog.run()
        clock.advance(timedelta(minutes=1))
    assert len(sender.sent) == 1


async def test_recovered_job_is_reported_and_can_alert_again(
    health: JobHealth, watchdog: JobWatchdog, sender: FakeAlertSender, clock: FakeClock
) -> None:
    clock.advance(LIMIT + timedelta(minutes=1))
    await watchdog.run()
    health.started("remnawave_reconcile")
    health.succeeded("remnawave_reconcile")
    await watchdog.run()
    # дольше окна склейки тревог, чтобы новая тревога не склеилась с первой
    clock.advance(timedelta(minutes=11))
    await watchdog.run()

    assert len(sender.sent) == 3
    assert "снова работает" in sender.sent[1].text


async def test_not_stale_right_after_start(health: JobHealth, watchdog: JobWatchdog) -> None:
    await watchdog.run()
    assert health.stale() == []


def test_unknown_job_mark_is_an_error(health: JobHealth) -> None:
    with pytest.raises(UnknownJobError):
        health.started("nope")


def test_same_job_cannot_be_watched_twice(health: JobHealth) -> None:
    with pytest.raises(ValueError, match="уже"):
        health.watch("remnawave_reconcile", LIMIT)
