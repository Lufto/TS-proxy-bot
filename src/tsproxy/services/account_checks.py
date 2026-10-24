"""Проверки прокси-аккаунтов: общий вход для вебхуков Remnawave и сверки (задача 09).

Вебхук Remnawave — только сигнал «с аккаунтом что-то произошло» (аудит §4.2): из него
берём лишь имя аккаунта, а состояние перечитываем из API. Сверка раз в 5 минут делает
то же самое для всех аккаунтов бота — страховка от потерянных вебхуков (аудит R05:
панель повторяет отправку всего 3 раза за 15 секунд).

И вебхук, и сверка передают свежий `ProxyAccount` одним и тем же обработчикам
(`AccountCheckHandler`). Обработчики решают по **текущему состоянию**, а не по имени
события, и должны быть идемпотентными: один и тот же аккаунт приходит к ним
и по вебхуку, и по каждой сверке. Точки подключения:
- задача 10 — напоминания за день и за час, продления пробного, прощание;
- задача 11 — льготный период по окончании платного срока (`domain/grace.py`).

Трогаем только аккаунты бота (`tg_<id>` в боевом режиме, `dev_<id>` в squad `test`
в разработке): события о нынешних клиентах панели пропускаем, не перечитывая.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from tsproxy.config import RunMode
from tsproxy.integrations.remnawave_types import (
    DEV_USERNAME_PREFIX,
    PROD_USERNAME_PREFIX,
    AccountGuard,
    AccountNotFound,
    ForeignAccountError,
    ProxyAccount,
    RemnawaveClient,
)
from tsproxy.services.clock import Clock, utc_now

logger = logging.getLogger(__name__)


class CheckSource(StrEnum):
    """Откуда пришёл повод проверить аккаунт."""

    WEBHOOK = "webhook"
    RECONCILIATION = "reconciliation"


@dataclass(frozen=True)
class AccountCheck:
    """Повод перепроверить прокси-аккаунт клиента.

    `account` — свежее состояние из API. `event` — имя события Remnawave
    (`user.expired`), только подсказка для логов: решать по нему нельзя, оно могло
    устареть или потеряться. У сверки события нет.
    """

    telegram_id: int
    account: ProxyAccount
    source: CheckSource
    event: str | None
    checked_at: datetime


class AccountCheckHandler(Protocol):
    """Обработчик проверки аккаунта (задачи 10, 11). Должен быть идемпотентным."""

    async def handle(self, check: AccountCheck) -> None: ...


class WebhookOutcome(StrEnum):
    """Чем кончилась обработка вебхука об аккаунте."""

    CHECKED = "checked"  # перечитали, все обработчики отработали
    FAILED = "failed"  # перечитали, но хотя бы один обработчик упал (повторит сверка)
    FOREIGN = "foreign"  # аккаунт не бота — пропустили
    NOT_FOUND = "not_found"  # аккаунта в панели уже нет


@dataclass(frozen=True)
class CheckReport:
    """Итог сверки: сколько аккаунтов проверено и сколько раз упали обработчики."""

    accounts: int
    failures: int


class AccountChecks:
    """Передаёт свежее состояние аккаунтов бота обработчикам — по вебхуку и по сверке."""

    def __init__(
        self,
        remnawave: RemnawaveClient,
        mode: RunMode,
        clock: Clock,
        handlers: Sequence[AccountCheckHandler],
    ) -> None:
        self._remnawave = remnawave
        self._guard = AccountGuard(mode)
        self._prefix = PROD_USERNAME_PREFIX if mode is RunMode.PROD else DEV_USERNAME_PREFIX
        self._clock = clock
        self._handlers = tuple(handlers)
        # Вебхук и сверка об одном аккаунте не обрабатываются одновременно.
        # Записей столько, сколько клиентов, — для сотен клиентов это мелочь.
        self._locks: dict[int, asyncio.Lock] = {}

    def telegram_id_of(self, username: str) -> int | None:
        """Telegram ID по имени аккаунта бота; `None` — аккаунт не бота в этом режиме."""
        digits = username.removeprefix(self._prefix)
        if digits == username or not digits.isascii() or not digits.isdigit():
            return None
        telegram_id = int(digits)
        try:
            # Обратная проверка отсекает `tg_0`, `tg_007` и прочие неканонические имена
            canonical = self._guard.username_for(telegram_id)
        except ForeignAccountError:
            return None
        return telegram_id if canonical == username else None

    async def on_webhook(self, event: str, username: str) -> WebhookOutcome:
        """Вебхук об аккаунте: перечитать его из API и передать обработчикам.

        Ошибки панели (`RemnawaveUnavailable` и др.) пробрасываются: событие догонит сверка.
        """
        telegram_id = self.telegram_id_of(username)
        if telegram_id is None:
            logger.info("Вебхук %s о чужом аккаунте — пропускаем", event)
            return WebhookOutcome.FOREIGN
        try:
            account = await self._remnawave.get_account(telegram_id)
        except AccountNotFound:
            # нормальная ситуация для `user.deleted`; обработчикам проверять нечего
            logger.info("Вебхук %s: аккаунта клиента %s в панели нет", event, telegram_id)
            return WebhookOutcome.NOT_FOUND
        except ForeignAccountError:
            # в разработке: имя `dev_<id>`, но аккаунт не в squad `test`
            logger.info("Вебхук %s: аккаунт клиента %s не принадлежит боту", event, telegram_id)
            return WebhookOutcome.FOREIGN
        check = AccountCheck(telegram_id, account, CheckSource.WEBHOOK, event, utc_now(self._clock))
        failures = await self._dispatch(check)
        return WebhookOutcome.FAILED if failures else WebhookOutcome.CHECKED

    async def check_all(self) -> CheckReport:
        """Сверка: передать обработчикам каждый аккаунт бота. Чужие отфильтрованы обёрткой.

        Ошибки панели при чтении списка пробрасываются вызывающему (сверке).
        """
        accounts = await self._remnawave.list_accounts()
        now = utc_now(self._clock)
        checked = 0
        failures = 0
        for account in accounts:
            telegram_id = self.telegram_id_of(account.username)
            if telegram_id is None:
                # обёртка уже отфильтровала чужих; сюда попадать не должно
                logger.error("Сверка: аккаунт с чужим именем в списке бота — пропускаем")
                continue
            check = AccountCheck(telegram_id, account, CheckSource.RECONCILIATION, None, now)
            failures += await self._dispatch(check)
            checked += 1
        return CheckReport(accounts=checked, failures=failures)

    async def _dispatch(self, check: AccountCheck) -> int:
        """Передать проверку всем обработчикам по очереди. Возвращает число упавших."""
        lock = self._locks.setdefault(check.telegram_id, asyncio.Lock())
        failures = 0
        async with lock:
            for handler in self._handlers:
                try:
                    await handler.handle(check)
                # Ошибка одного обработчика не должна оставить без проверки остальные
                # обработчики и остальных клиентов. Не прячем: трассировка в лог, число
                # упавших — вызывающему (сверка поднимет тревогу), следующая сверка повторит.
                except Exception:
                    failures += 1
                    logger.exception(
                        "Обработчик %s упал на клиенте %s (%s)",
                        type(handler).__name__,
                        check.telegram_id,
                        check.source.value,
                    )
        return failures
