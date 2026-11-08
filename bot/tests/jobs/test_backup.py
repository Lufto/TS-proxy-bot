"""Бэкап базы бота целиком: шифрование, хранение, отправка в «💾 Бэкапы», тревоги."""

import gzip
import shutil
import sqlite3
import stat
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from backup_helpers import (
    ALERTS_THREAD_ID,
    BACKUPS_THREAD_ID,
    CHAT_ID,
    CLIENT_NAMES,
    client_names,
    names_in,
)

from tsproxy.config import Settings
from tsproxy.jobs.age import AgeEncryptor, CommandFailedError, SubprocessRunner
from tsproxy.jobs.backup import DatabaseBackup, run_daily_backup
from tsproxy.jobs.backup_files import (
    BackupNotSupportedError,
    daily_name,
    monthly_name,
)
from tsproxy.jobs.fakes import (
    FAKE_AGE_HEADER,
    FakeCommandRunner,
    FakeDocumentSender,
    SentDocument,
)
from tsproxy.services.alerts import Alerter
from tsproxy.services.fakes import FakeAlertSender, FakeClock

TODAY = date(2026, 10, 24)
FILE_MODE = 0o600
DIR_MODE = 0o700
FAILING_EXIT_CODE = 3


def decode_fake_backup(path: Path, target: Path) -> None:
    """Обратное к подделке шифрования: снять метку и распаковать gzip."""
    data = path.read_bytes()
    assert data.startswith(FAKE_AGE_HEADER)
    target.write_bytes(gzip.decompress(data[len(FAKE_AGE_HEADER) :]))


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def make_backup(
    database_url: str,
    backup_dir: Path,
    encryptor: AgeEncryptor,
    documents: FakeDocumentSender,
    alerter: Alerter,
    clock: FakeClock,
) -> DatabaseBackup:
    return DatabaseBackup(
        database_url=database_url,
        backup_dir=backup_dir,
        encryptor=encryptor,
        documents=documents,
        alerter=alerter,
        chat_id=CHAT_ID,
        thread_id=BACKUPS_THREAD_ID,
        clock=clock,
    )


async def test_backup_is_stored_daily_and_monthly_with_private_modes(
    backup: DatabaseBackup, backup_dir: Path, tmp_path: Path
) -> None:
    result = await run_daily_backup(backup)

    daily = backup_dir / "daily" / daily_name(TODAY)
    monthly = backup_dir / "monthly" / monthly_name(TODAY)
    assert (result.stored.daily, result.stored.monthly) == (daily, monthly)
    assert daily.read_bytes() == monthly.read_bytes()
    assert result.stored.size_bytes == daily.stat().st_size
    assert mode(daily) == FILE_MODE
    assert mode(monthly) == FILE_MODE
    for directory in (backup_dir, backup_dir / "daily", backup_dir / "monthly"):
        assert mode(directory) == DIR_MODE
    restored = tmp_path / "restored.db"
    decode_fake_backup(daily, restored)
    assert client_names(restored) == list(CLIENT_NAMES)


async def test_no_plain_copy_or_temporary_files_left(
    backup: DatabaseBackup, backup_dir: Path
) -> None:
    await backup.run()
    assert names_in(backup_dir) == ["daily", "monthly"]
    assert names_in(backup_dir / "daily") == [daily_name(TODAY)]


async def test_encryption_uses_age_with_recipients_file(
    backup: DatabaseBackup, runner: FakeCommandRunner, recipients: Path
) -> None:
    await backup.run()

    assert len(runner.calls) == 1
    args = runner.calls[0]
    assert args[:6] == [
        "age",
        "--encrypt",
        "--recipients-file",
        str(recipients),
        "--output",
        args[5],
    ]
    assert args[5].endswith(".db.gz.age")
    assert args[6].endswith(".db.gz")
    assert len(args) == 7


async def test_copy_is_sent_to_backups_topic(
    backup: DatabaseBackup, documents: FakeDocumentSender
) -> None:
    result = await backup.run()

    assert result.sent_to_telegram
    assert len(documents.sent) == 1
    sent = documents.sent[0]
    assert sent == SentDocument(CHAT_ID, BACKUPS_THREAD_ID, result.stored.daily, sent.caption)
    assert "2026-10-24" in sent.caption


async def test_file_name_uses_moscow_date(backup: DatabaseBackup, clock: FakeClock) -> None:
    clock.now = datetime(2026, 10, 31, 22, 30, tzinfo=UTC)  # 1 ноября, 01:30 МСК
    result = await backup.run()
    assert result.stored.daily.name == "tsproxy-2026-11-01.db.gz.age"
    assert result.stored.monthly.name == "tsproxy-2026-11.db.gz.age"


async def test_telegram_failure_keeps_local_copy_and_alerts(
    backup: DatabaseBackup,
    documents: FakeDocumentSender,
    alert_sender: FakeAlertSender,
    backup_dir: Path,
) -> None:
    old = backup_dir / "daily" / daily_name(date(2026, 9, 1))
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    documents.error = RuntimeError("Telegram не отвечает")

    result = await backup.run()

    assert not result.sent_to_telegram
    assert result.stored.daily.exists()
    assert result.stored.monthly.exists()
    assert result.removed == (old,)  # чистка идёт как обычно
    assert len(alert_sender.sent) == 1
    alert = alert_sender.sent[0]
    assert (alert.chat_id, alert.thread_id) == (CHAT_ID, ALERTS_THREAD_ID)
    assert "не отправлен в Telegram" in alert.text
    assert "Telegram не отвечает" in alert.text


async def test_encryption_failure_alerts_raises_and_keeps_old_copies(
    backup: DatabaseBackup,
    runner: FakeCommandRunner,
    alert_sender: FakeAlertSender,
    documents: FakeDocumentSender,
    backup_dir: Path,
) -> None:
    old = backup_dir / "daily" / daily_name(date(2026, 9, 1))
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    runner.error = CommandFailedError("age: код 1: нет файла ключа")

    with pytest.raises(CommandFailedError):
        await backup.run()

    assert len(alert_sender.sent) == 1
    assert "не получился" in alert_sender.sent[0].text
    assert "нет файла ключа" in alert_sender.sent[0].text
    assert documents.sent == []
    assert old.exists()  # без свежей копии старые не удаляем
    assert names_in(backup_dir) == ["daily"]  # временной папки нет


async def test_missing_database_alerts_and_raises(
    backup: DatabaseBackup, database: Path, alert_sender: FakeAlertSender
) -> None:
    shutil.rmtree(database.parent)
    with pytest.raises(sqlite3.OperationalError, match="unable to open"):
        await backup.run()
    assert len(alert_sender.sent) == 1
    assert names_in(database.parent.parent) == ["backups", "recipients.txt"]  # базу не создали


async def test_old_daily_copies_removed_monthly_kept(
    backup: DatabaseBackup, backup_dir: Path
) -> None:
    old_day = date(2026, 9, 30)
    kept_day = date(2026, 10, 11)
    for directory, name in (
        ("daily", daily_name(old_day)),
        ("daily", daily_name(kept_day)),
        ("monthly", monthly_name(old_day)),
    ):
        (backup_dir / directory).mkdir(parents=True, exist_ok=True)
        (backup_dir / directory / name).write_bytes(b"copy")

    result = await backup.run()

    assert result.removed == (backup_dir / "daily" / daily_name(old_day),)
    assert names_in(backup_dir / "daily") == [
        daily_name(kept_day),
        daily_name(TODAY),
    ]
    assert names_in(backup_dir / "monthly") == [
        monthly_name(old_day),
        monthly_name(TODAY),
    ]


def test_non_sqlite_database_rejected_at_startup(
    backup_dir: Path,
    recipients: Path,
    runner: FakeCommandRunner,
    documents: FakeDocumentSender,
    alerter: Alerter,
    clock: FakeClock,
) -> None:
    with pytest.raises(BackupNotSupportedError, match="только SQLite"):
        make_backup(
            "postgresql+asyncpg://bot:pass@localhost/tsproxy",
            backup_dir,
            AgeEncryptor(recipients, runner),
            documents,
            alerter,
            clock,
        )


def test_from_settings_uses_backup_settings(
    monkeypatch: pytest.MonkeyPatch,
    documents: FakeDocumentSender,
    alerter: Alerter,
) -> None:
    for key, value in {
        "BOT_TOKEN": "1:abc",
        "REMNAWAVE_TOKEN": "rw",
        "ADMIN_IDS": "111",
        "ADMIN_CHAT_ID": "-100123",
        "ALERTS_THREAD_ID": "2",
        "BACKUPS_THREAD_ID": "3",
        "DATABASE_URL": "postgresql+asyncpg://bot:pass@localhost/tsproxy",
    }.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.backup_dir == Path("/var/backups/tsproxy")
    assert settings.backup_age_recipients == Path("/etc/remnawave-backup/recipients.txt")
    with pytest.raises(BackupNotSupportedError):
        DatabaseBackup.from_settings(settings, documents, alerter)


async def test_subprocess_runner_reports_exit_code_and_stderr() -> None:
    script = f"import sys; sys.stderr.write('сломалось'); sys.exit({FAILING_EXIT_CODE})"
    with pytest.raises(CommandFailedError, match=f"код {FAILING_EXIT_CODE}: сломалось"):
        await SubprocessRunner().run([sys.executable, "-c", script])


async def test_subprocess_runner_succeeds_silently() -> None:
    await SubprocessRunner().run([sys.executable, "-c", "pass"])


# --- настоящий age: зашифровать копию и расшифровать секретным ключом ---

AGE = shutil.which("age")
AGE_KEYGEN = shutil.which("age-keygen")


@pytest.fixture
def age_key(tmp_path: Path) -> tuple[Path, Path]:
    """Одноразовая пара ключей: (секретный ключ, файл открытого ключа)."""
    assert AGE_KEYGEN is not None
    identity = tmp_path / "key.txt"
    subprocess.run([AGE_KEYGEN, "-o", str(identity)], check=True, capture_output=True)  # noqa: S603
    public = subprocess.run(  # noqa: S603
        [AGE_KEYGEN, "-y", str(identity)], check=True, capture_output=True, text=True
    ).stdout
    recipients = tmp_path / "real-recipients.txt"
    recipients.write_text(public)
    return identity, recipients


@pytest.mark.skipif(AGE is None or AGE_KEYGEN is None, reason="age не установлен")
async def test_real_age_round_trip(
    database: Path,
    backup_dir: Path,
    age_key: tuple[Path, Path],
    documents: FakeDocumentSender,
    alerter: Alerter,
    clock: FakeClock,
    tmp_path: Path,
) -> None:
    identity, recipients = age_key
    backup = make_backup(
        f"sqlite+aiosqlite:///{database}",
        backup_dir,
        AgeEncryptor(recipients, SubprocessRunner()),
        documents,
        alerter,
        clock,
    )

    result = await backup.run()

    encrypted = result.stored.daily.read_bytes()
    assert encrypted.startswith(b"age-encryption.org/v1")
    packed = tmp_path / "restored.db.gz"
    await SubprocessRunner().run(
        ["age", "--decrypt", "-i", str(identity), "-o", str(packed), str(result.stored.daily)]
    )
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(packed.read_bytes()))
    assert client_names(restored) == list(CLIENT_NAMES)
