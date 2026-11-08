"""Общее для тестов бэкапа: живая база как у бота и константы."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

REPO_CONTENT = Path(__file__).parent.parent.parent / "content"
REPO_CONFIG = Path(__file__).parent.parent.parent.parent / "config"
# 04:30 МСК 24 октября
NOW = datetime(2026, 10, 24, 1, 30, tzinfo=UTC)
CHAT_ID = -100123
ALERTS_THREAD_ID = 2
BACKUPS_THREAD_ID = 3
CLIENT_NAMES = ("Иван", "Мария", "Пётр")


def create_live_database(path: Path) -> None:
    """База как у бота: режим WAL и пара строк."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE clients (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        db.executemany("INSERT INTO clients (name) VALUES (?)", [(n,) for n in CLIENT_NAMES])
        db.commit()


def client_names(path: Path) -> list[str]:
    with closing(sqlite3.connect(path)) as db:
        return [row[0] for row in db.execute("SELECT name FROM clients ORDER BY id")]


def names_in(directory: Path) -> list[str]:
    """Имена файлов и папок внутри, по алфавиту."""
    return sorted(entry.name for entry in directory.iterdir())
