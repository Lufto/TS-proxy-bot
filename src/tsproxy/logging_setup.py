"""Логирование с маскировкой секретов и ссылок подписки (задача «Логи и мониторинг»)."""

import logging
import re
from collections.abc import Iterable

# Ссылка подписки даёт доступ к прокси: в логах оставляем только последние 4 символа кода.
# Старый адрес панели `/api/sub/<код>` и новый `sub.ts-proxy.com/<код>`.
SUBSCRIPTION_URL_RE = re.compile(
    r"https?://(?:sub\.[\w.-]+|[\w.-]+/api/sub)/([A-Za-z0-9_-]+)",
)
MASK = "****"
# httpx пишет полные адреса запросов, а в них бывают токены; aiosqlite на DEBUG пишет
# параметры SQL-запросов, то есть тексты переписки поддержки (их в логи не пишем)
QUIET_LOGGERS = ("httpx", "aiosqlite")
VISIBLE_TAIL = 4


def mask_subscription_urls(text: str) -> str:
    return SUBSCRIPTION_URL_RE.sub(lambda m: f"https://…/{MASK}{m[1][-VISIBLE_TAIL:]}", text)


class MaskingFilter(logging.Filter):
    """Подменяет секреты и ссылки подписки в готовом тексте записи."""

    def __init__(self, secrets: Iterable[str]) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def mask(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, MASK)
        return mask_subscription_urls(text)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.mask(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.mask(record.exc_text)
        return True


def setup_logging(level: str, secrets: Iterable[str]) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(MaskingFilter(secrets))
    logging.basicConfig(level=level, handlers=[handler], force=True)
    quiet_library_loggers()


def quiet_library_loggers() -> None:
    """Библиотеки, которые на уровне DEBUG/INFO пишут лишнее, — только предупреждения и выше."""
    for name in QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
