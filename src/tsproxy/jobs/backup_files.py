"""Работа с файлами бэкапа базы бота (задача 23): копия SQLite, проверка, сжатие, хранение.

Все функции здесь синхронные и блокирующие (диск, SQLite). Из асинхронного кода их вызывают
только через `asyncio.to_thread`, чтобы бот не замирал на время копирования.
"""

import gzip
import os
import re
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy.engine import make_url

BACKUP_NAME_PREFIX = "tsproxy-"
BACKUP_SUFFIX = ".db.gz.age"
DAILY_DIR_NAME = "daily"
MONTHLY_DIR_NAME = "monthly"
# Ежедневные копии храним 2 недели (задача 08): сегодняшняя и 13 предыдущих
DAILY_KEEP_DAYS = 14
DIR_MODE = 0o700  # папки с копиями видит только владелец
FILE_MODE = 0o600  # файлы копий читает и пишет только владелец
GZIP_LEVEL = 9
SQLITE_OK = "ok"  # ответ `PRAGMA integrity_check` для целой базы

_DAILY_NAME = re.compile(
    rf"^{re.escape(BACKUP_NAME_PREFIX)}(\d{{4}}-\d{{2}}-\d{{2}}){re.escape(BACKUP_SUFFIX)}$"
)


@dataclass(frozen=True)
class StoredBackup:
    """Где легла копия и сколько она весит."""

    daily: Path
    monthly: Path
    size_bytes: int


class BackupError(Exception):
    """Бэкап базы бота не получился."""


class BackupNotSupportedError(BackupError):
    """Бэкап умеет только SQLite: для другой базы его ещё не сделали."""


def sqlite_database_path(database_url: str) -> Path:
    """Путь к файлу SQLite из `DATABASE_URL`. Для другой базы — понятная ошибка при запуске."""
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        raise BackupNotSupportedError(
            f"Бэкап базы бота пока умеет только SQLite, а в DATABASE_URL — "
            f"{url.get_backend_name()}. Для PostgreSQL бэкап ещё не сделан."
        )
    if not url.database or url.database == ":memory:":
        raise BackupNotSupportedError("Бэкап базы в памяти невозможен: в DATABASE_URL нет файла")
    return Path(url.database)


def daily_name(day: date) -> str:
    """Имя ежедневной копии: `tsproxy-2026-10-24.db.gz.age`."""
    return f"{BACKUP_NAME_PREFIX}{day.isoformat()}{BACKUP_SUFFIX}"


def monthly_name(day: date) -> str:
    """Имя месячной копии: `tsproxy-2026-10.db.gz.age` (за месяц остаётся последняя)."""
    return f"{BACKUP_NAME_PREFIX}{day:%Y-%m}{BACKUP_SUFFIX}"


def expired_daily_names(names: list[str], today: date) -> list[str]:
    """Какие ежедневные копии пора удалить: старше `DAILY_KEEP_DAYS` дней по дате в имени.

    Чужие файлы (имя не по шаблону) не трогаем никогда.
    """
    oldest_kept = today - timedelta(days=DAILY_KEEP_DAYS - 1)
    expired = []
    for name in names:
        match = _DAILY_NAME.match(name)
        if match is not None and date.fromisoformat(match.group(1)) < oldest_kept:
            expired.append(name)
    return sorted(expired)


def copy_sqlite(source: Path, target: Path) -> None:
    """Согласованная копия живой базы через online backup API SQLite.

    Копия делается за один шаг под блокировкой чтения: в режиме WAL бот в это время
    продолжает писать, а в копию попадает состояние на момент начала (только завершённые
    транзакции). Источник открываем только для чтения — несуществующий файл не создастся.
    `closing`: контекст `sqlite3.Connection` сам соединение не закрывает.
    """
    source_uri = source.resolve().as_uri() + "?mode=ro"
    with (
        closing(sqlite3.connect(source_uri, uri=True)) as src,
        closing(sqlite3.connect(target)) as dst,
    ):
        src.backup(dst)


def check_integrity(path: Path) -> None:
    """Убедиться, что копия открывается и цела (`PRAGMA integrity_check`)."""
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        rows = db.execute("PRAGMA integrity_check").fetchall()
    result = [row[0] for row in rows]
    if result != [SQLITE_OK]:
        raise BackupError(f"Копия базы повреждена: {'; '.join(map(str, result))}")


def gzip_file(source: Path, target: Path) -> None:
    """Сжать файл gzip."""
    with source.open("rb") as raw, gzip.open(target, "wb", compresslevel=GZIP_LEVEL) as packed:
        shutil.copyfileobj(raw, packed)


def make_private_dir(path: Path) -> None:
    """Создать папку (с родителями) и закрыть её от всех, кроме владельца."""
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(DIR_MODE)


def store_backup(encrypted: Path, backup_dir: Path, day: date) -> StoredBackup:
    """Положить зашифрованную копию в `daily/` и `monthly/` (права 600).

    Файлы появляются под окончательным именем целиком (`os.replace`), поэтому
    оборванная запись не оставит половинку копии с правильным именем.
    """
    daily_dir = backup_dir / DAILY_DIR_NAME
    monthly_dir = backup_dir / MONTHLY_DIR_NAME
    make_private_dir(daily_dir)
    make_private_dir(monthly_dir)
    daily = daily_dir / daily_name(day)
    monthly = monthly_dir / monthly_name(day)
    _place(encrypted, daily, copy=True)
    _place(encrypted, monthly, copy=False)
    return StoredBackup(daily=daily, monthly=monthly, size_bytes=daily.stat().st_size)


def remove_expired_daily(backup_dir: Path, today: date) -> list[Path]:
    """Удалить ежедневные копии старше двух недель. Месячные не трогаем никогда."""
    daily_dir = backup_dir / DAILY_DIR_NAME
    names = [entry.name for entry in daily_dir.iterdir() if entry.is_file()]
    removed = [daily_dir / name for name in expired_daily_names(names, today)]
    for path in removed:
        path.unlink()
    return removed


def _place(source: Path, target: Path, *, copy: bool) -> None:
    """Перенести (или скопировать) файл на место через временное имя рядом с целью."""
    temporary = target.with_name(target.name + ".tmp")
    if copy:
        shutil.copyfile(source, temporary)
    else:
        shutil.move(source, temporary)
    temporary.chmod(FILE_MODE)
    os.replace(temporary, target)
