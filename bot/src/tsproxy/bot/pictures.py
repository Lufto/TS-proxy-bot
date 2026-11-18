"""Картинки экранов в Telegram: файл загружается один раз, дальше — по `file_id`
(спека «Картинки и профиль бота», задача 27).

После первой отправки Telegram отвечает сообщением с `file_id` фото; повторно бот шлёт
этот `file_id`, а не файл. `file_id` живёт в памяти процесса (база не меняется): после
перезапуска каждая картинка загрузится заново один раз. `file_id` привязан к боту,
поэтому тестовый и боевой бот загружают картинки каждый сам.

Если Telegram отверг запомненный `file_id` (ошибка про файл: сменили токен бота, Telegram
забыл файл), бот забывает его и один раз загружает файл заново (`send`). Другой отказ
(подпись, кнопки) повторной загрузкой не лечится и поднимается как есть.

Экран с картинкой (`Screen.picture`) отправляет `ClientDelivery`: фото с подписью
(до 1024 символов) и кнопками; живое меню убирает его так же, как текст.
"""

import logging
from collections.abc import Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InputFile, Message

from tsproxy.content.pictures import Picture, PictureFiles

logger = logging.getLogger(__name__)

# Отказ Telegram из-за самого файла, например «wrong file identifier/HTTP URL specified»
FILE_REJECTED = "file"


class TelegramPictures:
    """Что передать Telegram вместо картинки: файл или `file_id` прошлой загрузки."""

    def __init__(self, files: PictureFiles) -> None:
        self._files = files
        self._file_ids: dict[Picture, str] = {}

    async def send(
        self, picture: Picture, request: Callable[[InputFile | str], Awaitable[Message]]
    ) -> Message:
        """Отправить фото запросом `request(photo)` и запомнить его `file_id`.

        Запомненный `file_id` отвергнут из-за файла — забыть его и загрузить файл заново,
        один раз.
        """
        photo = self._photo(picture)
        try:
            sent = await request(photo)
        except TelegramBadRequest as error:
            if not isinstance(photo, str) or FILE_REJECTED not in error.message.lower():
                raise
            logger.warning(
                "Telegram отверг file_id картинки %s (%s), загружаем заново", picture, error
            )
            self._file_ids.pop(picture, None)
            sent = await request(self._photo(picture))
        self._remember(picture, sent)
        return sent

    def _photo(self, picture: Picture) -> InputFile | str:
        """`file_id`, если картинка уже загружена, иначе файл для загрузки."""
        file_id = self._file_ids.get(picture)
        if file_id is not None:
            return file_id
        return FSInputFile(self._files.path(picture))

    def _remember(self, picture: Picture, sent: Message) -> None:
        """Запомнить `file_id` из присланного фото: самый крупный размер — последний."""
        if sent.photo:
            self._file_ids[picture] = sent.photo[-1].file_id
