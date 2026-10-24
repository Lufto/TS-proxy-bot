"""Настройки бота из `.env`. Секреты хранятся как SecretStr, чтобы не попасть в логи."""

import re
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class RunMode(StrEnum):
    """Где запущен бот. От режима зависит, какие прокси-аккаунты боту разрешено трогать."""

    DEV = "dev"  # Mac разработчика: только squad `test` и аккаунты `dev_`
    PROD = "prod"  # сервер: только аккаунты `tg_`


# Секрет вебхука Telegram: Telegram принимает 1–256 символов `A-Za-z0-9_-`,
# аудит (R01) требует не меньше 32 — тогда его не подобрать перебором.
WEBHOOK_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
# Секрет подписи вебхуков Remnawave = WEBHOOK_SECRET_HEADER панели. Панель 3.4.5 требует
# не меньше 32 символов, только латинские буквы и цифры (app-config/config.schema.ts).
REMNAWAVE_WEBHOOK_SECRET_RE = re.compile(r"^[A-Za-z0-9]{32,}$")
DEFAULT_WEB_PORT = 8080


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
    # Squad, в который бот кладёт новые прокси-аккаунты в боевом режиме (в разработке — `test`)
    remnawave_squad: str = "Default-Squad"
    # Вебхуки Remnawave (задача 09): тот же секрет, что WEBHOOK_SECRET_HEADER в `.env` панели.
    # Не задан — вебхуки не принимаются, события догоняет только сверка раз в 5 минут.
    remnawave_webhook_secret: SecretStr | None = None

    # Боевой режим: Telegram шлёт апдейты на PUBLIC_BASE_URL + путь вебхука (без токена бота)
    # и подписывает их секретом TELEGRAM_WEBHOOK_SECRET. В разработке не нужны (polling).
    public_base_url: str | None = None
    telegram_webhook_secret: SecretStr | None = None
    # Где слушает веб-сервер (вебхуки). Снаружи к нему ходит только веб-сервер-посредник.
    web_host: str = "127.0.0.1"
    web_port: int = DEFAULT_WEB_PORT

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
    # Поддельная платёжка (PAYMENTS_MODE=fake): файл с платежами, общий со скриптом
    # `scripts/simulate_payment.py`
    fake_payments_file: Path = Path("data/fake_payments.json")
    log_level: str = "INFO"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> object:
        """`ADMIN_IDS=111,222` → {111, 222}."""
        if isinstance(value, str):
            return frozenset(int(part) for part in value.split(",") if part.strip())
        return value

    @field_validator(
        "public_base_url", "telegram_webhook_secret", "remnawave_webhook_secret", mode="before"
    )
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        """Пустая строка в `.env` (`PUBLIC_BASE_URL=`) значит «не задано»."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _check_webhook(self) -> Self:
        """В боевом режиме без адреса и секрета вебхука бот не стартует (аудит R01)."""
        secret = self.telegram_webhook_secret
        if secret is not None and not WEBHOOK_SECRET_RE.fullmatch(secret.get_secret_value()):
            raise ValueError("TELEGRAM_WEBHOOK_SECRET: от 32 до 256 символов A-Z, a-z, 0-9, _ и -")
        if self.run_mode is RunMode.PROD:
            if self.public_base_url is None or not self.public_base_url.startswith("https://"):
                raise ValueError("PUBLIC_BASE_URL: в режиме prod нужен адрес https://…")
            if secret is None:
                raise ValueError("TELEGRAM_WEBHOOK_SECRET: в режиме prod обязателен")
        return self

    @model_validator(mode="after")
    def _check_remnawave_webhook(self) -> Self:
        """Секрет вебхуков Remnawave — по правилам панели, иначе подписи не совпадут."""
        secret = self.remnawave_webhook_secret
        if secret is not None and not REMNAWAVE_WEBHOOK_SECRET_RE.fullmatch(
            secret.get_secret_value()
        ):
            raise ValueError("REMNAWAVE_WEBHOOK_SECRET: от 32 символов, только A-Z, a-z и 0-9")
        return self

    @model_validator(mode="after")
    def _check_payments(self) -> Self:
        """Поддельная платёжка в боевом режиме засчитала бы оплату, которой не было (аудит R23)."""
        if self.run_mode is RunMode.PROD and self.payments_mode is PaymentsMode.FAKE:
            raise ValueError("PAYMENTS_MODE=fake нельзя в режиме prod")
        return self

    def secret_values(self) -> list[str]:
        """Все секреты — для маскировки в логах."""
        values = [self.bot_token.get_secret_value(), self.remnawave_token.get_secret_value()]
        if self.telegram_webhook_secret is not None:
            values.append(self.telegram_webhook_secret.get_secret_value())
        if self.remnawave_webhook_secret is not None:
            values.append(self.remnawave_webhook_secret.get_secret_value())
        return values


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # значения приходят из окружения
