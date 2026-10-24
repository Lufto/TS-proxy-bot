"""Точка запуска: `uv run tsproxy`.

Пока только проверяет настройки и тексты; сам бот появится в задаче 06.
"""

import logging
import sys

from pydantic import ValidationError

from tsproxy.config import load_settings
from tsproxy.content.loader import ContentError, load_content
from tsproxy.logging_setup import setup_logging

logger = logging.getLogger(__name__)


def main() -> int:
    try:
        settings = load_settings()
    except ValidationError as error:
        missing = ", ".join(str(e["loc"][0]).upper() for e in error.errors())
        print(f"Ошибка в .env, проверьте переменные: {missing}", file=sys.stderr)
        return 1
    setup_logging(settings.log_level, settings.secret_values())
    try:
        content = load_content(settings.content_dir)
    except ContentError as error:
        logger.error("Тексты или тарифы с ошибкой: %s", error)
        return 1
    logger.info(
        "Настройки и тексты в порядке: режим %s, тарифов %d",
        settings.run_mode,
        len(content.tariffs),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
