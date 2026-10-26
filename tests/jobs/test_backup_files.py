"""Файлы бэкапа: согласованная копия живой базы, проверка целостности, сроки хранения."""

import sqlite3
import threading
from contextlib import closing
from datetime import date
from pathlib import Path

import pytest
from backup_helpers import CLIENT_NAMES, client_names

from tsproxy.jobs.backup_files import (
    BackupError,
    BackupNotSupportedError,
    check_integrity,
    copy_sqlite,
    daily_name,
    expired_daily_names,
    expired_monthly_names,
    monthly_name,
    remove_expired,
    sqlite_database_path,
)

TODAY = date(2026, 10, 24)
COPIES_DURING_WRITES = 20
WRITER_BUSY_TIMEOUT_S = 5
SQLITE_PAGE_SIZE = 4096
CORRUPTED_PAGES = 2


def test_copy_skips_uncommitted_transaction(database: Path, tmp_path: Path) -> None:
    copy = tmp_path / "copy.db"
    with closing(sqlite3.connect(database, isolation_level=None)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO clients (name) VALUES ('не зафиксирован')")
        copy_sqlite(database, copy)  # бот в это время держит открытую транзакцию записи
        writer.execute("COMMIT")

    check_integrity(copy)
    assert client_names(copy) == list(CLIENT_NAMES)
    assert len(client_names(database)) == len(CLIENT_NAMES) + 1


def test_copy_during_concurrent_writes_is_consistent(database: Path, tmp_path: Path) -> None:
    """Писатель добавляет строки парами в одной транзакции: в целой копии их всегда чётное число."""
    with closing(sqlite3.connect(database)) as db:
        db.execute("DELETE FROM clients")
        db.commit()
    started = threading.Event()
    stop = threading.Event()

    def write_pairs() -> None:
        with closing(sqlite3.connect(database, timeout=WRITER_BUSY_TIMEOUT_S)) as db:
            while not stop.is_set():
                with db:  # одна транзакция на пару
                    db.execute("INSERT INTO clients (name) VALUES ('пара-1')")
                    db.execute("INSERT INTO clients (name) VALUES ('пара-2')")
                started.set()

    writer = threading.Thread(target=write_pairs)
    writer.start()
    counts = []
    try:
        assert started.wait(WRITER_BUSY_TIMEOUT_S)
        for index in range(COPIES_DURING_WRITES):
            copy = tmp_path / f"copy-{index}.db"
            copy_sqlite(database, copy)
            check_integrity(copy)
            counts.append(len(client_names(copy)))
    finally:
        stop.set()
        writer.join()
    assert all(count > 0 and count % 2 == 0 for count in counts)
    assert len(client_names(database)) >= counts[-1]


def test_copy_of_missing_database_fails_and_creates_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        copy_sqlite(missing, tmp_path / "copy.db")
    assert not missing.exists()


def test_integrity_check_passes_for_healthy_copy(database: Path) -> None:
    check_integrity(database)


def test_integrity_check_rejects_corrupted_file(database: Path) -> None:
    with closing(sqlite3.connect(database)) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.execute("PRAGMA journal_mode=DELETE")
    data = bytearray(database.read_bytes())
    # затираем всё после первой страницы (заголовок и схема остаются)
    data[SQLITE_PAGE_SIZE:] = b"\xff" * (len(data) - SQLITE_PAGE_SIZE)
    data += b"\xff" * SQLITE_PAGE_SIZE * CORRUPTED_PAGES
    database.write_bytes(bytes(data))
    with pytest.raises((BackupError, sqlite3.DatabaseError)):
        check_integrity(database)


def test_backup_names() -> None:
    assert daily_name(TODAY) == "tsproxy-2026-10-24.db.gz.age"
    assert monthly_name(TODAY) == "tsproxy-2026-10.db.gz.age"


def test_daily_copies_older_than_two_weeks_expire() -> None:
    names = [
        daily_name(date(2026, 10, 24)),  # сегодня
        daily_name(date(2026, 10, 11)),  # 13 дней назад — 14-я копия, остаётся
        daily_name(date(2026, 10, 10)),  # 14 дней назад — удаляется
        daily_name(date(2025, 1, 1)),
        monthly_name(date(2025, 1, 1)),  # имя месячной — не по шаблону ежедневной
        "notes.txt",
        "tsproxy-2026-10-01.db.gz.age.tmp",
    ]
    assert expired_daily_names(names, TODAY) == [
        daily_name(date(2025, 1, 1)),
        daily_name(date(2026, 10, 10)),
    ]


def test_monthly_copies_older_than_three_years_expire() -> None:
    names = [
        monthly_name(date(2026, 10, 1)),  # этот месяц
        monthly_name(date(2023, 11, 1)),  # 35 месяцев назад — 36-я копия, остаётся
        monthly_name(date(2023, 10, 1)),  # 36 месяцев назад — удаляется
        monthly_name(date(2020, 1, 1)),
        daily_name(date(2020, 1, 1)),  # имя ежедневной — не по шаблону месячной
        "notes.txt",
        "tsproxy-2020-01.db.gz.age.tmp",
    ]
    assert expired_monthly_names(names, TODAY) == [
        monthly_name(date(2020, 1, 1)),
        monthly_name(date(2023, 10, 1)),
    ]


def test_retention_keeps_recent_monthly_and_foreign_files(tmp_path: Path) -> None:
    daily_dir = tmp_path / "daily"
    monthly_dir = tmp_path / "monthly"
    daily_dir.mkdir()
    monthly_dir.mkdir()
    old_day = date(2024, 1, 31)
    ancient = date(2022, 1, 31)
    (daily_dir / daily_name(old_day)).write_bytes(b"old")
    (daily_dir / daily_name(TODAY)).write_bytes(b"new")
    for name in (monthly_name(ancient), monthly_name(old_day), daily_name(old_day)):
        (monthly_dir / name).write_bytes(b"monthly")

    removed = remove_expired(tmp_path, TODAY)

    assert removed == [daily_dir / daily_name(old_day), monthly_dir / monthly_name(ancient)]
    assert sorted(p.name for p in daily_dir.iterdir()) == [daily_name(TODAY)]
    assert sorted(p.name for p in monthly_dir.iterdir()) == sorted(
        [monthly_name(old_day), daily_name(old_day)]
    )


def test_sqlite_url_gives_database_path() -> None:
    url = "sqlite+aiosqlite:///data/tsproxy.db"
    assert sqlite_database_path(url) == Path("data/tsproxy.db")


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://bot:secret@localhost/tsproxy",
        "sqlite+aiosqlite:///:memory:",
        "sqlite+aiosqlite://",
    ],
)
def test_unsupported_database_is_rejected(url: str) -> None:
    with pytest.raises(BackupNotSupportedError):
        sqlite_database_path(url)
