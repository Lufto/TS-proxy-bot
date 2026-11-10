"""Сверка с Remnawave раз в 5 минут и при запуске (задача 09, аудит R05, §7.3).

Вебхуки Remnawave теряются: панель повторяет отправку всего 3 раза за 15 секунд, а бот
в это время может перезапускаться. Сверка перечитывает все аккаунты бота и все ноды
и передаёт их тем же обработчикам, что и вебхуки. Обработчики идемпотентны, поэтому
сверка, нашедшая уже обработанное, ничего не повторяет.

Сверка не падает: недоступная панель или упавший обработчик — тревога администраторам
(одинаковые склеиваются) и запись в лог; следующая сверка попробует снова.
"""

import logging
from collections.abc import Callable

from tsproxy.content.loader import Texts
from tsproxy.integrations.remnawave_types import (
    RemnawaveAuthError,
    RemnawaveError,
    RemnawaveUnavailable,
)
from tsproxy.services.account_checks import AccountChecks
from tsproxy.services.alerts import Alerter
from tsproxy.services.nodes import NodeMonitor

logger = logging.getLogger(__name__)

# Тот же ключ, что у тревоги «Remnawave не отвечает» в боте (`bot/connect.py`):
# все тревоги о недоступной панели склеиваются в одну.
REMNAWAVE_UNAVAILABLE_ALERT = "remnawave_unavailable"
RECONCILE_UNAVAILABLE_ALERT = "reconcile_unavailable"
RECONCILE_ERROR_ALERT = "reconcile_error"
RECONCILE_FAILED_ALERT = "reconcile_failed"
# Панель не ответила или не приняла токен — это сбой панели, а не сверки
PANEL_DOWN_ERRORS = (RemnawaveUnavailable, RemnawaveAuthError)


class Reconciliation:
    """Одна сверка: аккаунты бота, затем ноды. Каждая часть — независимо от другой."""

    def __init__(
        self,
        accounts: AccountChecks,
        nodes: NodeMonitor,
        alerter: Alerter,
        admin_texts: Texts,
    ) -> None:
        self._accounts = accounts
        self._nodes = nodes
        self._alerter = alerter
        self._texts = admin_texts

    async def run(self) -> None:
        await self._check_accounts()
        await self._check_nodes()

    async def _check_accounts(self) -> None:
        try:
            report = await self._accounts.check_all()
        except RemnawaveError as error:
            await self._panel_failed(self._check_accounts, "accounts", error)
            return
        logger.info("Сверка: проверено аккаунтов %d, ошибок %d", report.accounts, report.failures)
        if report.failures:
            await self._alerter.alert(
                RECONCILE_FAILED_ALERT, source=self._check_accounts, count=report.failures
            )

    async def _check_nodes(self) -> None:
        try:
            await self._nodes.refresh()
        except RemnawaveError as error:
            await self._panel_failed(self._check_nodes, "nodes", error)

    async def _panel_failed(
        self, source: Callable[..., object], part: str, error: RemnawaveError
    ) -> None:
        """Панель не дала данные: тревога, сверка повторится в следующий раз. `part` —
        что читали: `accounts` или `nodes` (текст — `reconcile_parts.<part>`)."""
        logger.warning("Сверка (%s) не прошла: %s", part, error)
        what = self._texts.get(f"reconcile_parts.{part}")
        if isinstance(error, PANEL_DOWN_ERRORS):
            await self._alerter.alert(
                RECONCILE_UNAVAILABLE_ALERT,
                source=source,
                error=error,
                glue=REMNAWAVE_UNAVAILABLE_ALERT,
                part=what,
            )
            return
        await self._alerter.alert(RECONCILE_ERROR_ALERT, source=source, error=error, part=what)
