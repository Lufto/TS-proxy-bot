"""Настоящая отправка файлов через aiogram (реализует `jobs.backup.DocumentSender`)."""

from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile


class TelegramDocumentSender:
    """Шлёт файл в тему группы. Ошибки Telegram летят дальше — их ловит вызывающий код."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def send_document(
        self, chat_id: int, thread_id: int | None, path: Path, caption: str
    ) -> None:
        await self._bot.send_document(
            chat_id, FSInputFile(path), message_thread_id=thread_id, caption=caption
        )
