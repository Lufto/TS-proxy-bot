from pathlib import Path

import pytest
from backup_helpers import (
    ALERTS_THREAD_ID,
    BACKUPS_THREAD_ID,
    CHAT_ID,
    NOW,
    REPO_CONTENT,
    create_live_database,
)

from tsproxy.content.loader import load_content
from tsproxy.jobs.age import AgeEncryptor
from tsproxy.jobs.backup import DatabaseBackup
from tsproxy.jobs.fakes import FakeCommandRunner, FakeDocumentSender
from tsproxy.logging_setup import MaskingFilter
from tsproxy.services.alerts import Alerter
from tsproxy.services.fakes import FakeAlertSender, FakeClock


@pytest.fixture
def database(tmp_path: Path) -> Path:
    # пробел в пути — проверка, что путь не ломается при открытии базы и запуске age
    path = tmp_path / "data dir" / "tsproxy.db"
    create_live_database(path)
    return path


@pytest.fixture
def backup_dir(tmp_path: Path) -> Path:
    return tmp_path / "backups"


@pytest.fixture
def recipients(tmp_path: Path) -> Path:
    path = tmp_path / "recipients.txt"
    path.write_text("age1fake\n")
    return path


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
def alert_sender() -> FakeAlertSender:
    return FakeAlertSender()


@pytest.fixture
def alerter(alert_sender: FakeAlertSender, clock: FakeClock) -> Alerter:
    return Alerter(
        sender=alert_sender,
        texts=load_content(REPO_CONTENT).admin_texts,
        chat_id=CHAT_ID,
        thread_id=ALERTS_THREAD_ID,
        clock=clock,
        mask=MaskingFilter([]).mask,
    )


@pytest.fixture
def runner() -> FakeCommandRunner:
    return FakeCommandRunner()


@pytest.fixture
def documents() -> FakeDocumentSender:
    return FakeDocumentSender()


@pytest.fixture
def backup(
    database: Path,
    backup_dir: Path,
    recipients: Path,
    runner: FakeCommandRunner,
    documents: FakeDocumentSender,
    alerter: Alerter,
    clock: FakeClock,
) -> DatabaseBackup:
    return DatabaseBackup(
        database_url=f"sqlite+aiosqlite:///{database}",
        backup_dir=backup_dir,
        encryptor=AgeEncryptor(recipients, runner),
        documents=documents,
        alerter=alerter,
        chat_id=CHAT_ID,
        thread_id=BACKUPS_THREAD_ID,
        clock=clock,
    )
