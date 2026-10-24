"""Приём апдейтов Telegram по вебхуку (боевой режим), аудит R01.

Адрес вебхука не содержит токен бота. Telegram подписывает каждый запрос заголовком
`X-Telegram-Bot-Api-Secret-Token` с секретом из `setWebhook`; без него или с чужим
секретом запрос отклоняется (403), иначе любой, кто узнал адрес, слал бы апдейты
от имени любого Telegram ID, включая администраторов.
"""

import hmac
import logging
from dataclasses import dataclass

from aiogram import Bot, Dispatcher
from aiogram.methods import TelegramMethod
from aiogram.types import Update
from fastapi import APIRouter, Request, Response, status
from pydantic import ValidationError

logger = logging.getLogger(__name__)

TELEGRAM_WEBHOOK_PATH = "/telegram/webhook"
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"  # noqa: S105  # имя заголовка, не секрет


def webhook_url(public_base_url: str) -> str:
    """Полный адрес вебхука для `setWebhook`."""
    return public_base_url.rstrip("/") + TELEGRAM_WEBHOOK_PATH


def secret_matches(received: str | None, expected: str) -> bool:
    """Сверка секрета за постоянное время: по времени ответа секрет не подобрать."""
    if received is None:
        return False
    return hmac.compare_digest(received.encode(), expected.encode())


@dataclass(frozen=True)
class TelegramWebhook:
    """Всё, что нужно приёмнику апдейтов: бот, диспетчер и ожидаемый секрет."""

    bot: Bot
    dispatcher: Dispatcher
    secret: str

    def router(self) -> APIRouter:
        router = APIRouter()
        router.add_api_route(
            TELEGRAM_WEBHOOK_PATH, self.receive, methods=["POST"], include_in_schema=False
        )
        return router

    async def receive(self, request: Request) -> Response:
        if not secret_matches(request.headers.get(SECRET_HEADER), self.secret):
            logger.warning("Вебхук Telegram: запрос без верного секрета отклонён")
            return Response(status_code=status.HTTP_403_FORBIDDEN)
        try:
            update = Update.model_validate(await request.json(), context={"bot": self.bot})
        except (ValueError, ValidationError) as error:
            # Секрет верный, а тело не апдейт — сломанный запрос, повтор не поможет
            logger.warning("Вебхук Telegram: тело запроса не апдейт: %s", type(error).__name__)
            return Response(status_code=status.HTTP_400_BAD_REQUEST)
        result = await self.dispatcher.feed_webhook_update(self.bot, update)
        if isinstance(result, TelegramMethod):
            # Обработчик вернул «метод в ответ на вебхук» — выполняем его обычным запросом
            await self.bot(result)
        return Response(status_code=status.HTTP_200_OK)
