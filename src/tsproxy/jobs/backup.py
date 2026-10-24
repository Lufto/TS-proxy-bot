"""Ежедневный бэкап базы бота (задача 23, решения задач 08 и 22).

Порядок, как у бэкапов панели (`ops/remnawave-backup/`):

1. Согласованная копия SQLite без остановки бота (online backup API, в отдельном потоке).
2. Проверка копии `PRAGMA integrity_check`.
3. Сжатие gzip и шифрование `age` открытым ключом (тот же, что у панели).
4. Копия ложится в `daily/tsproxy-ГГГГ-ММ-ДД.db.gz.age` и `monthly/tsproxy-ГГГГ-ММ.db.gz.age`
   (права 600). Месячная перезаписывается каждый день, поэтому за месяц остаётся последняя.
5. Файл уходит в тему «💾 Бэкапы».
6. Ежедневные копии старше двух недель удаляются, месячные хранятся всегда.

Дата в имени — по Москве, как у панели. Любой сбой — запись в лог и тревога в «🚨 Тревоги».
Если не получилась сама копия, `run` бросает исключение дальше (сторож фоновых задач
увидит сбой), а старые копии не удаляются. Если не ушло только сообщение в Telegram,
локальная копия остаётся, чистка идёт как обычно, а наружу — только тревога.

Запуск по расписанию (04:30 МСК, через полчаса после бэкапа панели) подключает
планировщик фоновых задач в отдельной задаче: он вызывает `run_daily_backup`.
"""

import asyncio
import logging
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from tsproxy.config import Settings
from tsproxy.domain.dates import MOSCOW
from tsproxy.jobs.age import AgeEncryptor, SubprocessRunner
from tsproxy.jobs.backup_files import (
    StoredBackup,
    check_integrity,
    copy_sqlite,
    gzip_file,
    make_private_dir,
    remove_expired_daily,
    sqlite_database_path,
    store_backup,
)
from tsproxy.services.alerts import Alerter
from tsproxy.services.clock import Clock, system_clock, utc_now

logger = logging.getLogger(__name__)

ALERT_BACKUP_FAILED = "bot_backup_failed"
ALERT_BACKUP_NOT_SENT = "bot_backup_not_sent"
# Боты Telegram не могут отправить файл больше 50 МБ
TELEGRAM_DOCUMENT_LIMIT_BYTES = 50 * 1024 * 1024
BYTES_PER_KIB = 1024
WORK_DIR_PREFIX = ".work-"
_PLAIN_NAME = "tsproxy.db"
_PACKED_NAME = "tsproxy.db.gz"
_ENCRYPTED_NAME = "tsproxy.db.gz.age"


class DocumentSender(Protocol):
    """Отправка файла в тему группы. Настоящая (aiogram) — в задаче 06, в тестах — подделка."""

    async def send_document(
        self, chat_id: int, thread_id: int | None, path: Path, caption: str
    ) -> None: ...


class Encryptor(Protocol):
    """Шифрование файла `source` в `target`. Настоящее — `AgeEncryptor`."""

    async def encrypt(self, source: Path, target: Path) -> None: ...


@dataclass(frozen=True)
class BackupResult:
    """Чем закончился бэкап: где копии, что удалено и ушла ли копия в Telegram."""

    stored: StoredBackup
    removed: tuple[Path, ...]
    sent_to_telegram: bool


class DatabaseBackup:
    """Делает, хранит и отправляет зашифрованные копии базы бота."""

    def __init__(
        self,
        database_url: str,
        backup_dir: Path,
        encryptor: Encryptor,
        documents: DocumentSender,
        alerter: Alerter,
        chat_id: int,
        thread_id: int | None,
        clock: Clock,
    ) -> None:
        # Для не-SQLite база ошибка случается здесь, то есть при запуске бота, а не ночью
        self._database = sqlite_database_path(database_url)
        self._backup_dir = backup_dir
        self._encryptor = encryptor
        self._documents = documents
        self._alerter = alerter
        self._chat_id = chat_id
        self._thread_id = thread_id
        self._clock = clock

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        documents: DocumentSender,
        alerter: Alerter,
        clock: Clock = system_clock,
    ) -> "DatabaseBackup":
        """Боевая сборка: настоящий `age`, папки и тема «💾 Бэкапы» из настроек."""
        return cls(
            database_url=settings.database_url,
            backup_dir=settings.backup_dir,
            encryptor=AgeEncryptor(settings.backup_age_recipients, SubprocessRunner()),
            documents=documents,
            alerter=alerter,
            chat_id=settings.admin_chat_id,
            thread_id=settings.backups_thread_id,
            clock=clock,
        )

    async def run(self) -> BackupResult:
        """Сделать копию за сегодня (по Москве), отправить её и почистить старые."""
        day = utc_now(self._clock).astimezone(MOSCOW).date()
        try:
            stored = await self._make_local_copy(day)
        # Ловим всё: причин много (диск, SQLite, age), и о любой должен узнать администратор.
        # Ошибку не прячем: тревога, лог и исключение дальше.
        except Exception as error:
            await self._fail(f"Бэкап базы бота за {day} не получился: {error}")
            raise
        logger.info("Бэкап базы бота готов: %s (%s байт)", stored.daily, stored.size_bytes)

        sent = await self._send(stored, day)

        try:
            removed = await asyncio.to_thread(remove_expired_daily, self._backup_dir, day)
        except OSError as error:
            await self._fail(f"Не удалось удалить старые бэкапы базы бота: {error}")
            raise
        for path in removed:
            logger.info("Удалён старый бэкап базы бота: %s", path)
        return BackupResult(stored=stored, removed=tuple(removed), sent_to_telegram=sent)

    async def _make_local_copy(self, day: date) -> StoredBackup:
        work = await asyncio.to_thread(self._create_work_dir)
        try:
            plain = work / _PLAIN_NAME
            packed = work / _PACKED_NAME
            encrypted = work / _ENCRYPTED_NAME
            await asyncio.to_thread(self._snapshot, plain, packed)
            await self._encryptor.encrypt(packed, encrypted)
            return await asyncio.to_thread(store_backup, encrypted, self._backup_dir, day)
        finally:
            # во временной папке лежит незашифрованная копия — убираем её в любом случае
            await asyncio.to_thread(shutil.rmtree, work)

    def _create_work_dir(self) -> Path:
        """Временная папка внутри папки бэкапов (права 700), на том же диске, что и копии."""
        make_private_dir(self._backup_dir)
        return Path(tempfile.mkdtemp(prefix=WORK_DIR_PREFIX, dir=self._backup_dir))

    def _snapshot(self, plain: Path, packed: Path) -> None:
        """Копия базы → проверка целостности → gzip. Блокирующая: только через `to_thread`."""
        copy_sqlite(self._database, plain)
        check_integrity(plain)
        gzip_file(plain, packed)

    async def _send(self, stored: StoredBackup, day: date) -> bool:
        """Отправить копию в «💾 Бэкапы». Сбой — тревога, но локальная копия остаётся."""
        if stored.size_bytes > TELEGRAM_DOCUMENT_LIMIT_BYTES:
            await self._not_sent(
                f"Бэкап базы бота за {day} не отправлен в Telegram: файл "
                f"{format_size(stored.size_bytes)} больше лимита Telegram. "
                f"Копия на сервере: {stored.daily}"
            )
            return False
        caption = f"💾 Бэкап базы бота {day} ({format_size(stored.size_bytes)})"
        try:
            await self._documents.send_document(
                self._chat_id, self._thread_id, stored.daily, caption
            )
        # Ловим всё: ошибки Telegram и сети бывают разных типов, а локальная копия уже есть.
        # Ошибку не прячем: лог с подробностями и тревога.
        except Exception as error:
            logger.exception("Бэкап базы бота не отправлен в Telegram")
            await self._not_sent(
                f"Бэкап базы бота за {day} не отправлен в Telegram: {error}. "
                f"Копия на сервере: {stored.daily}"
            )
            return False
        return True

    async def _fail(self, text: str) -> None:
        logger.exception("%s", text)
        await self._alerter.raise_alert(ALERT_BACKUP_FAILED, text)

    async def _not_sent(self, text: str) -> None:
        logger.error("%s", text)
        await self._alerter.raise_alert(ALERT_BACKUP_NOT_SENT, text)


async def run_daily_backup(backup: DatabaseBackup) -> BackupResult:
    """Точка входа для планировщика: раз в сутки в 04:30 МСК (подключается отдельной задачей)."""
    return await backup.run()


def format_size(size_bytes: int) -> str:
    """Размер для подписи: «812 Б», «3.4 КБ», «12.0 МБ»."""
    if size_bytes < BYTES_PER_KIB:
        return f"{size_bytes} Б"
    size = size_bytes / BYTES_PER_KIB
    for unit in ("КБ", "МБ"):
        if size < BYTES_PER_KIB:
            return f"{size:.1f} {unit}"
        size /= BYTES_PER_KIB
    return f"{size:.1f} ГБ"
