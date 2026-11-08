"""Присмотр за бэкапами панели Remnawave (задача 22 в `vpn-bot-rework`, требование владельца
2026-10-24).

Бэкап панели делает скрипт `ops/remnawave-backup/remnawave-backup.sh` (cron, 04:00 МСК).
Он сам шлёт копию в «💾 Бэкапы», но если не запустился вовсе — тишина. Поэтому бот
смотрит на две вещи:

- папку ежедневных копий — есть ли копия моложе 26 часов;
- журнал скрипта `backup.log` — ушла ли последняя копия в Telegram.

Строки журнала пишет скрипт: `<время ISO 8601> <сообщение>`, например
`2026-10-24T01:00:03+00:00 OK: /var/backups/remnawave/daily/remnawave-2026-10-24.tar.gz.age (208K)`.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from tsproxy.domain.dates import require_aware

# Скрипт идёт раз в сутки; 2 часа запаса — на медленный дамп и перевод часов сервера
PANEL_BACKUP_MAX_AGE = timedelta(hours=26)
# Сообщения скрипта, по которым бот судит о бэкапе (см. `remnawave-backup.sh`)
LOG_OK_PREFIX = "OK: "
LOG_FAIL_PREFIX = "FAIL"
LOG_FILE_NOT_SENT = "telegram: файл не отправлен"
# «/путь/к/копии (208K)» — размер в скобках в конце
_SIZE_SUFFIX_START = " ("


class PanelBackupProblem(StrEnum):
    """Что не так с бэкапом панели — о каждом поднимается тревога."""

    STALE = "stale"  # нет копии моложе 26 часов
    NOT_SENT = "not_sent"  # копия на сервере есть, в Telegram не ушла


@dataclass(frozen=True)
class PanelBackupLog:
    """Что известно из журнала скрипта."""

    last_ok_at: datetime | None  # когда записана последняя удачная копия
    last_ok_copy: str | None  # путь к ней на сервере
    last_failure_at: datetime | None  # последний сбой скрипта (строка «FAIL»)
    sent: bool  # после последней удачной копии нет «telegram: файл не отправлен»


def parse_backup_log(lines: Iterable[str]) -> PanelBackupLog:
    """Разобрать журнал скрипта. Непонятные и обрезанные строки пропускаются."""
    last_ok_at: datetime | None = None
    last_ok_copy: str | None = None
    last_failure_at: datetime | None = None
    sent = True
    for line in lines:
        parsed = _parse_line(line)
        if parsed is None:
            continue
        moment, message = parsed
        if message.startswith(LOG_OK_PREFIX):
            last_ok_at = moment
            last_ok_copy = message.removeprefix(LOG_OK_PREFIX).split(_SIZE_SUFFIX_START, 1)[0]
            sent = True
        elif message.startswith(LOG_FAIL_PREFIX):
            last_failure_at = moment
        elif message.startswith(LOG_FILE_NOT_SENT) and last_ok_at is not None:
            sent = False
    return PanelBackupLog(last_ok_at, last_ok_copy, last_failure_at, sent=sent)


def _parse_line(line: str) -> tuple[datetime, str] | None:
    stamp, _, message = line.strip().partition(" ")
    if not message:
        return None
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None  # время без пояса — не от нашего скрипта (`date -Is` пишет пояс)
    return moment, message


def panel_backup_problems(
    log: PanelBackupLog, newest_copy_at: datetime | None, now: datetime
) -> frozenset[PanelBackupProblem]:
    """Проблемы бэкапа панели. `newest_copy_at` — время самой свежей копии в папке."""
    problems: set[PanelBackupProblem] = set()
    if newest_copy_at is None or require_aware(now) - newest_copy_at > PANEL_BACKUP_MAX_AGE:
        problems.add(PanelBackupProblem.STALE)
    if not log.sent:
        problems.add(PanelBackupProblem.NOT_SENT)
    return frozenset(problems)
