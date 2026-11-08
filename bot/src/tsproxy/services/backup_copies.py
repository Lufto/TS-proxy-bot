"""Самая свежая копия в папке бэкапов — для присмотра и ежедневного отчёта (задача 22).

Бот только смотрит на имена, время и размер файлов, но не читает их содержимое.
Чтение папки — блокирующее, поэтому снаружи вызывается через `asyncio.to_thread`.
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
from pathlib import Path


@dataclass(frozen=True)
class BackupCopy:
    path: Path
    modified_at: datetime  # UTC
    size_bytes: int


def newest_copy(directory: Path, pattern: str) -> BackupCopy | None:
    """Самая свежая (по времени изменения) копия по шаблону имени. Нет папки — `None`.

    Нет прав на папку — `PermissionError` летит дальше: это ошибка настройки сервера.
    """
    # не `Path.is_dir()`: он молча отвечает «нет» и при нехватке прав
    try:
        with os.scandir(directory) as found:
            entries = list(found)
    except FileNotFoundError:
        return None
    newest: BackupCopy | None = None
    for entry in entries:
        if not fnmatchcase(entry.name, pattern) or not entry.is_file():
            continue
        stat = entry.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime, UTC)
        if newest is None or modified_at > newest.modified_at:
            newest = BackupCopy(Path(entry.path), modified_at, stat.st_size)
    return newest
