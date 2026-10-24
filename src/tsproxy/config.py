"""Настройки бота из `.env`. Секреты хранятся как SecretStr, чтобы не попасть в логи."""

from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class RunMode(StrEnum):
    """Где запущен бот. От режима зависит, какие прокси-аккаунты боту разрешено трогать."""

    DEV = "dev"  # Mac разработчика: только squad `test` и аккаунты `dev_`
    PROD = "prod"  # сервер: только аккаунты `tg_`


class PaymentsMode(StrEnum):
    FAKE = "fake"  # поддельная платёжка для разработки
    REAL = "real"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    run_mode: RunMode = RunMode.DEV
    payments_mode: PaymentsMode = PaymentsMode.FAKE

    bot_token: SecretStr
    remnawave_url: str = "https://panel.ts-proxy.com"
    remnawave_token: SecretStr

    admin_ids: Annotated[frozenset[int], NoDecode]
    admin_chat_id: int
    payments_thread_id: int | None = None
    alerts_thread_id: int
    backups_thread_id: int
    refunds_thread_id: int | None = None

    database_url: str = "sqlite+aiosqlite:///data/tsproxy.db"
    # Бэкапы базы бота (задача 23): куда класть и чем шифровать (открытый ключ age, как у панели)
    backup_dir: Path = Path("/var/backups/tsproxy")
    backup_age_recipients: Path = Path("/etc/remnawave-backup/recipients.txt")
    content_dir: Path = Path("content")
    log_level: str = "INFO"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> object:
        """`ADMIN_IDS=111,222` → {111, 222}."""
        if isinstance(value, str):
            return frozenset(int(part) for part in value.split(",") if part.strip())
        return value

    def secret_values(self) -> list[str]:
        """Все секреты — для маскировки в логах."""
        return [self.bot_token.get_secret_value(), self.remnawave_token.get_secret_value()]


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # значения приходят из окружения
