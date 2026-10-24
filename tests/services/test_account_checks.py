"""Проверки прокси-аккаунтов по сигналу (вебхук) и по сверке: одни и те же обработчики."""

import asyncio
from datetime import timedelta

import pytest
from conftest import NOW
from signal_helpers import RecordingHandler, create_account

from tsproxy.config import RunMode
from tsproxy.integrations.fakes import FakeRemnawave
from tsproxy.integrations.remnawave_types import (
    DEV_SQUAD_NAME,
    AccountStatus,
    RemnawaveUnavailable,
)
from tsproxy.services.account_checks import (
    AccountCheck,
    AccountChecks,
    CheckSource,
    WebhookOutcome,
)
from tsproxy.services.fakes import FakeClock

CLIENT_ID = 111
OTHER_ID = 222


@pytest.fixture
def remnawave(clock: FakeClock) -> FakeRemnawave:
    return FakeRemnawave(RunMode.PROD, now=clock)


@pytest.fixture
def handler() -> RecordingHandler:
    return RecordingHandler()


@pytest.fixture
def checks(remnawave: FakeRemnawave, handler: RecordingHandler, clock: FakeClock) -> AccountChecks:
    return AccountChecks(remnawave, RunMode.PROD, clock, [handler])


# --- Вебхук: только сигнал, состояние перечитываем ---


async def test_webhook_rereads_account_from_panel(
    checks: AccountChecks, remnawave: FakeRemnawave, handler: RecordingHandler
) -> None:
    await create_account(remnawave, CLIENT_ID)
    remnawave.set_status(CLIENT_ID, AccountStatus.EXPIRED)

    outcome = await checks.on_webhook("user.expired", f"tg_{CLIENT_ID}")

    assert outcome is WebhookOutcome.CHECKED
    [check] = handler.checks
    assert check.telegram_id == CLIENT_ID
    assert check.account.status is AccountStatus.EXPIRED  # из панели, не из вебхука
    assert check.source is CheckSource.WEBHOOK
    assert check.event == "user.expired"
    assert check.checked_at == NOW


@pytest.mark.parametrize(
    "username",
    ["ivan_petrov", "tg_", "tg_12a", "tg_111 ", "dev_111", "TG_111", "tg_0", "tg_-5", "x_tg_111"],
)
async def test_webhook_about_foreign_account_is_ignored(
    checks: AccountChecks, remnawave: FakeRemnawave, handler: RecordingHandler, username: str
) -> None:
    await create_account(remnawave, CLIENT_ID)

    outcome = await checks.on_webhook("user.expired", username)

    assert outcome is WebhookOutcome.FOREIGN
    assert handler.checks == []
    assert remnawave.writes == [("create", f"tg_{CLIENT_ID}")]


async def test_dev_mode_ignores_prod_accounts(clock: FakeClock, handler: RecordingHandler) -> None:
    remnawave = FakeRemnawave(RunMode.DEV, now=clock)
    checks = AccountChecks(remnawave, RunMode.DEV, clock, [handler])
    await create_account(remnawave, CLIENT_ID)

    assert await checks.on_webhook("user.expired", f"tg_{CLIENT_ID}") is WebhookOutcome.FOREIGN
    assert await checks.on_webhook("user.expired", f"dev_{CLIENT_ID}") is WebhookOutcome.CHECKED
    assert [c.account.squad_names for c in handler.checks] == [frozenset({DEV_SQUAD_NAME})]


async def test_dev_account_outside_test_squad_is_foreign(
    clock: FakeClock, handler: RecordingHandler
) -> None:
    remnawave = FakeRemnawave(RunMode.DEV, now=clock)
    checks = AccountChecks(remnawave, RunMode.DEV, clock, [handler])
    remnawave.add_foreign_account(f"dev_{CLIENT_ID}", squad_names={"Default-Squad"})

    assert await checks.on_webhook("user.expired", f"dev_{CLIENT_ID}") is WebhookOutcome.FOREIGN
    assert handler.checks == []


async def test_webhook_about_missing_account(
    checks: AccountChecks, handler: RecordingHandler
) -> None:
    outcome = await checks.on_webhook("user.deleted", f"tg_{CLIENT_ID}")

    assert outcome is WebhookOutcome.NOT_FOUND
    assert handler.checks == []


async def test_webhook_when_panel_unavailable_raises(
    checks: AccountChecks, remnawave: FakeRemnawave
) -> None:
    await create_account(remnawave, CLIENT_ID)
    remnawave.fail_next(RemnawaveUnavailable("нет связи"))

    with pytest.raises(RemnawaveUnavailable):
        await checks.on_webhook("user.expired", f"tg_{CLIENT_ID}")


# --- Сверка ---


async def test_reconciliation_finds_expiry_missed_while_bot_was_down(
    checks: AccountChecks, remnawave: FakeRemnawave, handler: RecordingHandler, clock: FakeClock
) -> None:
    await create_account(remnawave, CLIENT_ID)
    # Бот лежал: срок кончился, панель перевела аккаунт в EXPIRED, вебхук потерян
    clock.advance(timedelta(days=8))
    remnawave.set_status(CLIENT_ID, AccountStatus.EXPIRED)

    report = await checks.check_all()

    [check] = handler.checks
    assert check.telegram_id == CLIENT_ID
    assert check.account.status is AccountStatus.EXPIRED
    assert check.source is CheckSource.RECONCILIATION
    assert check.event is None
    assert check.checked_at == clock.now
    assert report.accounts == 1
    assert report.failures == 0


async def test_reconciliation_skips_foreign_accounts(
    checks: AccountChecks, remnawave: FakeRemnawave, handler: RecordingHandler
) -> None:
    remnawave.add_foreign_account("ivan_petrov", telegram_id=CLIENT_ID)
    remnawave.add_foreign_account("tg_old_client")
    await create_account(remnawave, OTHER_ID)

    await checks.check_all()

    assert [c.telegram_id for c in handler.checks] == [OTHER_ID]
    assert remnawave.writes == [("create", f"tg_{OTHER_ID}")]


async def test_failing_handler_does_not_stop_others(
    remnawave: FakeRemnawave, clock: FakeClock
) -> None:
    broken = RecordingHandler(error=RuntimeError("ошибка обработчика"))
    healthy = RecordingHandler()
    checks = AccountChecks(remnawave, RunMode.PROD, clock, [broken, healthy])
    await create_account(remnawave, CLIENT_ID)
    await create_account(remnawave, OTHER_ID)

    report = await checks.check_all()

    assert len(broken.checks) == 2
    assert [c.telegram_id for c in healthy.checks] == [CLIENT_ID, OTHER_ID]
    assert report.accounts == 2
    assert report.failures == 2


async def test_webhook_reports_handler_failures(remnawave: FakeRemnawave, clock: FakeClock) -> None:
    broken = RecordingHandler(error=RuntimeError("ошибка обработчика"))
    checks = AccountChecks(remnawave, RunMode.PROD, clock, [broken])
    await create_account(remnawave, CLIENT_ID)

    assert await checks.on_webhook("user.expired", f"tg_{CLIENT_ID}") is WebhookOutcome.FAILED


async def test_checks_of_one_account_do_not_overlap(
    remnawave: FakeRemnawave, clock: FakeClock
) -> None:
    """Вебхук и сверка об одном аккаунте одновременно: обработчики идут по очереди."""
    trace: list[str] = []

    class SlowHandler:
        async def handle(self, check: AccountCheck) -> None:
            trace.append(f"start:{check.source.value}")
            await asyncio.sleep(0.01)
            trace.append(f"end:{check.source.value}")

    checks = AccountChecks(remnawave, RunMode.PROD, clock, [SlowHandler()])
    await create_account(remnawave, CLIENT_ID)

    await asyncio.gather(checks.on_webhook("user.expired", f"tg_{CLIENT_ID}"), checks.check_all())

    assert trace[0].startswith("start") and trace[1].startswith("end")
    assert trace[2].startswith("start") and trace[3].startswith("end")
