"""Картинки экранов в Telegram: файл загружается один раз, дальше — по `file_id`
(спека «Картинки и профиль бота», задача 27).

После первой отправки Telegram отвечает сообщением с `file_id` фото; повторно бот шлёт
этот `file_id`, а не файл. `file_id` живёт в памяти процесса (база не меняется): после
перезапуска каждая картинка загрузится заново один раз. `file_id` привязан к боту,
поэтому тестовый и боевой бот загружают картинки каждый сам.

Если Telegram отверг запомненный `file_id` (любой отказ: текст ошибки не угадать — сменили
токен бота, Telegram забыл файл, …), бот забывает его и один раз загружает файл заново
(`send`): мёртвый `file_id` не остаётся в памяти до перезапуска. Отказ на загрузку самого
файла (подпись, кнопки) повторной загрузкой не лечится и поднимается как есть.

Первую загрузку картинки делает один запрос: если меню открыли сразу несколько клиентов,
остальные ждут её и получают картинку по `file_id` (замок на картинку).

Экран с картинкой (`Screen.picture`) отправляет `ClientDelivery`: фото с подписью
(до 1024 символов) и кнопками; живое меню убирает его так же, как текст.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputFile, Message

from tsproxy.content.pictures import Picture, PictureFiles

logger = logging.getLogger(__name__)


class TelegramPictures:
    """Что передать Telegram вместо картинки: файл или `file_id` прошлой загрузки."""

    def __init__(self, files: PictureFiles) -> None:
        self._files = files
        self._file_ids: dict[Picture, str] = {}
        # одна загрузка картинки за раз: одновременные первые отправки ждут её `file_id`
        self._uploads: defaultdict[Picture, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def send(
        self, picture: Picture, request: Callable[[InputFile | str], Awaitable[Message]]
    ) -> Message:
        """Отправить фото запросом `request(photo)` и запомнить его `file_id`.

        Запомненный `file_id` отвергнут — забыть его и загрузить файл заново, один раз.
        """
        file_id = self._file_ids.get(picture)
        if file_id is not None:
            try:
                return self._remember(picture, await request(file_id))
            except TelegramBadRequest as error:
                logger.warning(
                    "Telegram отверг file_id картинки %s (%s), загружаем заново", picture, error
                )
                if self._file_ids.get(picture) == file_id:
                    del self._file_ids[picture]
        return await self._upload(picture, request)

    async def _upload(
        self, picture: Picture, request: Callable[[InputFile | str], Awaitable[Message]]
    ) -> Message:
        """Загрузить файл. Пока ждали замок, картинку загрузил другой — шлём его `file_id`."""
        async with self._uploads[picture]:
            file_id = self._file_ids.get(picture)
            if file_id is None:
                photo = FSInputFile(self._files.path(picture))
                return self._remember(picture, await request(photo))
        return self._remember(picture, await request(file_id))

    def _remember(self, picture: Picture, sent: Message) -> Message:
        """Запомнить `file_id` из присланного фото: самый крупный размер — последний."""
        if sent.photo:
            self._file_ids[picture] = sent.photo[-1].file_id
        return sent
