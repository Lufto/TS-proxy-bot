"""Присмотр за бэкапами панели Remnawave (задача 22, требование владельца 2026-10-24).

Скрипт бэкапа панели (`ops/remnawave-backup/`, cron 04:00 МСК) сам отправляет копию
в «💾 Бэкапы», но если он не запустился вовсе — тишина. Бот раз в час смотрит:

- папку ежедневных копий (`PANEL_BACKUP_DIR`): нет копии моложе 26 часов → тревога;
- журнал скрипта (`PANEL_BACKUP_LOG`): после последней копии «telegram: файл не
  отправлен» → тревога «копия на сервере есть, в Telegram не ушла»;
- не получается прочитать журнал или папку (права) → тревога: присмотр не работает.

Каждая тревога — один раз на эпизод (пока та же проблема, бот молчит; новая копия,
снова не ушедшая в Telegram, — новый эпизод). Память о сказанном — в процессе:
после перезапуска бота о незакрытой проблеме скажем ещё раз, это приемлемо.

Содержимое копий бот не читает; доступ к `/etc/remnawave-backup/` ему не нужен.
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from tsproxy.content.loader import Texts
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.panel_backup import (
    PanelBackupLog,
    PanelBackupProblem,
    panel_backup_problems,
    parse_backup_log,
)
from tsproxy.services.alerts import Alerter
from tsproxy.services.backup_copies import BackupCopy, newest_copy
from tsproxy.services.clock import Clock, utc_now

logger = logging.getLogger(__name__)

# Имена копий, которые пишет `remnawave-backup.sh`
PANEL_COPY_PATTERN = "remnawave-*.tar.gz.age"
# Журнал растёт на несколько строк в сутки; читаем хвост, чтобы не зависеть от размера
LOG_TAIL_BYTES = 64 * 1024
PANEL_BACKUP_UNREADABLE_ALERT = "panel_backup_unreadable"
PANEL_BACKUP_ALERTS = {
    PanelBackupProblem.STALE: "panel_backup_stale",
    PanelBackupProblem.NOT_SENT: "panel_backup_not_sent",
}


class PanelBackupUnreadableError(Exception):
    """Журнал или папку копий не прочитать — присмотр за бэкапами панели не работает."""


@dataclass(frozen=True)
class PanelBackupStatus:
    newest: BackupCopy | None
    log: PanelBackupLog
    problems: frozenset[PanelBackupProblem]


class PanelBackupMonitor:
    """Смотрит на копии и журнал скрипта бэкапа панели, поднимает тревоги."""

    def __init__(
        self,
        log_path: Path,
        copies_dir: Path,
        alerter: Alerter,
        admin_texts: Texts,
        clock: Clock,
    ) -> None:
        self._log_path = log_path
        self._copies_dir = copies_dir
        self._alerter = alerter
        self._texts = admin_texts
        self._clock = clock
        self._alerted: set[tuple[str, str]] = set()  # (ключ тревоги, о чём именно)

    async def status(self) -> PanelBackupStatus:
        """Что с бэкапом панели сейчас. Не прочитать — `PanelBackupUnreadableError`."""
        try:
            newest, lines = await asyncio.to_thread(self._read)
        except OSError as error:
            raise PanelBackupUnreadableError(f"{type(error).__name__}: {error}") from error
        log = parse_backup_log(lines)
        problems = panel_backup_problems(
            log, newest.modified_at if newest is not None else None, utc_now(self._clock)
        )
        return PanelBackupStatus(newest, log, problems)

    async def check(self) -> None:
        """Фоновая задача: поднять тревогу о новой проблеме с бэкапом панели."""
        try:
            status = await self.status()
        except PanelBackupUnreadableError as error:
            logger.error("Бэкап панели: не прочитать журнал или папку копий: %s", error)
            current = {(PANEL_BACKUP_UNREADABLE_ALERT, "")}
            if not current <= self._alerted:
                await self._alerter.raise_alert(
                    PANEL_BACKUP_UNREADABLE_ALERT,
                    self._texts.get("alerts.panel_backup_unreadable", error=str(error)),
                )
            self._alerted = current
            return
        current = {self._signature(problem, status) for problem in status.problems}
        for problem in sorted(status.problems):
            if self._signature(problem, status) in self._alerted:
                continue
            logger.error("Бэкап панели: проблема %s", problem.value)
            await self._alerter.raise_alert(
                PANEL_BACKUP_ALERTS[problem], self._text(problem, status)
            )
        self._alerted = current

    def _read(self) -> tuple[BackupCopy | None, list[str]]:
        """Блокирующее чтение: только через `asyncio.to_thread`."""
        newest = newest_copy(self._copies_dir, PANEL_COPY_PATTERN)
        try:
            with self._log_path.open("rb") as log:
                size = log.seek(0, 2)
                log.seek(max(0, size - LOG_TAIL_BYTES))
                tail = log.read().decode("utf-8", errors="replace")
        except FileNotFoundError:
            # скрипт ещё ни разу не писал журнал — копий тоже нет, об этом скажет STALE
            tail = ""
        return newest, tail.splitlines()

    @staticmethod
    def _signature(problem: PanelBackupProblem, status: PanelBackupStatus) -> tuple[str, str]:
        if problem is PanelBackupProblem.NOT_SENT:
            return PANEL_BACKUP_ALERTS[problem], status.log.last_ok_copy or ""
        newest = status.newest
        return PANEL_BACKUP_ALERTS[problem], str(newest.path) if newest is not None else ""

    def _text(self, problem: PanelBackupProblem, status: PanelBackupStatus) -> str:
        if problem is PanelBackupProblem.NOT_SENT:
            return self._texts.get(
                "alerts.panel_backup_not_sent", copy=status.log.last_ok_copy or ""
            )
        newest = status.newest
        since = (
            format_moscow(newest.modified_at)
            if newest is not None
            else self._texts.get("alerts.panel_backup_never")
        )
        return self._texts.get("alerts.panel_backup_stale", since=since)
