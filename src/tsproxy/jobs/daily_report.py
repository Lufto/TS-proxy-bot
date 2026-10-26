"""Ежедневный отчёт в группу администраторов в 10:00 МСК (задача 18 в `vpn-bot-rework`,
задача 22).

За вчерашний день по Москве: новые клиенты, пробные, оплаты и выручка, тревоги. На сейчас:
оплаченные заказы без выданного доступа, открытые обращения, бэкапы базы бота и панели,
зависшие фоновые задачи. Тексты — `report.*` в `content/admin_texts.yaml`.

Тревоги считаются в памяти с запуска бота: если бот перезапускался вчера, в отчёте
сказано, с какого момента идёт счёт. Сбой отправки отчёта летит дальше — его увидит
сторож фоновых задач.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tsproxy.content.loader import Texts
from tsproxy.domain.dates import format_moscow
from tsproxy.domain.monitoring import DAILY_JOB_STALE_AFTER, ReportDay, previous_moscow_day
from tsproxy.domain.panel_backup import PanelBackupProblem
from tsproxy.jobs.backup import BackupResult, format_size
from tsproxy.jobs.backup_files import BACKUP_NAME_PREFIX, BACKUP_SUFFIX, DAILY_DIR_NAME
from tsproxy.services.alerts import Alerter, AlertSender
from tsproxy.services.backup_copies import BackupCopy, newest_copy
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.job_health import JobHealth
from tsproxy.services.panel_backups import PanelBackupMonitor, PanelBackupUnreadableError
from tsproxy.services.report_stats import day_stats

logger = logging.getLogger(__name__)

BOT_COPY_PATTERN = f"{BACKUP_NAME_PREFIX}*{BACKUP_SUFFIX}"
REPORT_DATE_FORMAT = "%d.%m.%Y"


class OverdueTickets(Protocol):
    """Обращения, где клиент ждёт ответа дольше часа (`SupportService`)."""

    async def overdue_tickets(self) -> Sequence[object]: ...


class Backup(Protocol):
    """Бэкап базы бота (`jobs.backup.DatabaseBackup`)."""

    async def run(self) -> BackupResult: ...


class BotBackupJob:
    """Бэкап базы бота по расписанию (04:30 МСК); помнит итог последнего запуска для отчёта."""

    def __init__(self, backup: Backup) -> None:
        self._backup = backup
        self.last: BackupResult | None = None

    async def run(self) -> None:
        self.last = await self._backup.run()


@dataclass(frozen=True)
class ReportDestination:
    chat_id: int
    thread_id: int | None  # None — общая тема группы


class DailyReport:
    """Собирает и отправляет ежедневный отчёт."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        support: OverdueTickets,
        alerter: Alerter,
        jobs: JobHealth,
        panel_backups: PanelBackupMonitor,
        bot_backup_dir: Path,
        bot_backup_job: BotBackupJob | None,
        sender: AlertSender,
        destination: ReportDestination,
        admin_texts: Texts,
        clock: Clock,
    ) -> None:
        """`bot_backup_job` — `None`, если бэкап базы бота не запускается (режим dev)."""
        self._session_factory = session_factory
        self._support = support
        self._alerter = alerter
        self._jobs = jobs
        self._panel_backups = panel_backups
        self._bot_backup_dir = bot_backup_dir
        self._bot_backup_job = bot_backup_job
        self._sender = sender
        self._destination = destination
        self._texts = admin_texts
        self._clock = clock

    async def send(self) -> None:
        text = await self.build()
        await self._sender.send(self._destination.chat_id, self._destination.thread_id, text)
        logger.info("Ежедневный отчёт отправлен")

    async def build(self) -> str:
        day = previous_moscow_day(utc_now(self._clock))
        lines = [
            *await self._sales_lines(day),
            self._alerts_line(day),
            await self._bot_backup_line(),
            await self._panel_backup_line(),
            self._jobs_line(),
        ]
        return "\n".join(lines)

    async def _sales_lines(self, day: ReportDay) -> list[str]:
        stats = await day_stats(self._session_factory, day)
        overdue = await self._support.overdue_tickets()
        lines = [
            self._texts.get("report.title", day=day.date.strftime(REPORT_DATE_FORMAT)),
            self._texts.get("report.new_clients", count=stats.new_clients),
            self._texts.get("report.trials", count=stats.trials),
            self._texts.get("report.payments", count=stats.payments, amount=stats.revenue),
        ]
        if stats.unfinished_orders:
            lines.append(self._texts.get("report.unfinished_orders", count=stats.unfinished_orders))
        lines.append(
            self._texts.get("report.tickets", open=stats.open_tickets, overdue=len(overdue))
        )
        return lines

    def _alerts_line(self, day: ReportDay) -> str:
        count = self._alerter.raised_on(day.date)
        since = self._alerter.counting_since
        if since > day.start:
            return self._texts.get(
                "report.alerts_since_start", count=count, since=format_moscow(since)
            )
        return self._texts.get("report.alerts", count=count)

    async def _bot_backup_line(self) -> str:
        if self._bot_backup_job is None:
            return self._texts.get("report.bot_backup_off")
        try:
            newest = await asyncio.to_thread(
                newest_copy, self._bot_backup_dir / DAILY_DIR_NAME, BOT_COPY_PATTERN
            )
        except OSError as error:
            logger.error("Отчёт: не прочитать папку бэкапов базы бота: %s", error)
            return self._texts.get("report.bot_backup_unreadable")
        if newest is None:
            return self._texts.get("report.bot_backup_none")
        if utc_now(self._clock) - newest.modified_at > DAILY_JOB_STALE_AFTER:
            return self._texts.get("report.bot_backup_stale", at=format_moscow(newest.modified_at))
        return self._texts.get(self._bot_sent_key(newest), **_copy_values(newest))

    def _bot_sent_key(self, newest: BackupCopy) -> str:
        job = self._bot_backup_job
        last = job.last if job is not None else None
        if last is None or last.stored.daily != newest.path:
            return "report.bot_backup_sent_unknown"  # копию делал бот до перезапуска
        return "report.bot_backup_sent" if last.sent_to_telegram else "report.bot_backup_not_sent"

    async def _panel_backup_line(self) -> str:
        try:
            status = await self._panel_backups.status()
        except PanelBackupUnreadableError as error:
            logger.error("Отчёт: не прочитать бэкапы панели: %s", error)
            return self._texts.get("report.panel_backup_unreadable")
        newest = status.newest
        if newest is None:
            return self._texts.get("report.panel_backup_none")
        if PanelBackupProblem.STALE in status.problems:
            return self._texts.get(
                "report.panel_backup_stale", at=format_moscow(newest.modified_at)
            )
        if PanelBackupProblem.NOT_SENT in status.problems:
            return self._texts.get("report.panel_backup_not_sent", **_copy_values(newest))
        return self._texts.get("report.panel_backup_ok", **_copy_values(newest))

    def _jobs_line(self) -> str:
        stale = sorted(state.name for state in self._jobs.stale())
        if stale:
            return self._texts.get("report.jobs_stale", names=", ".join(stale))
        return self._texts.get("report.jobs_ok")


def _copy_values(copy: BackupCopy) -> dict[str, str]:
    return {"at": format_moscow(copy.modified_at), "size": format_size(copy.size_bytes)}
