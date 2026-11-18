"""Картинки экранов в Telegram: файл загружается один раз, дальше — по `file_id`
(спека «Картинки и профиль бота», задача 27).

После первой отправки Telegram отвечает сообщением с `file_id` фото; повторно бот шлёт
этот `file_id`, а не файл. `file_id` живёт в памяти процесса (база не меняется): после
перезапуска каждая картинка загрузится заново один раз. `file_id` привязан к боту,
поэтому тестовый и боевой бот загружают картинки каждый сам.

Экран с картинкой (`Screen.picture`) отправляет `ClientDelivery`: фото с подписью
(до 1024 символов) и кнопками; живое меню убирает его так же, как текст.
"""

from aiogram.types import FSInputFile, InputFile, Message

from tsproxy.content.pictures import Picture, PictureFiles


class TelegramPictures:
    """Что передать Telegram вместо картинки: файл или `file_id` прошлой загрузки."""

    def __init__(self, files: PictureFiles) -> None:
        self._files = files
        self._file_ids: dict[Picture, str] = {}

    def photo(self, picture: Picture) -> InputFile | str:
        """`file_id`, если картинка уже загружена, иначе файл для загрузки."""
        file_id = self._file_ids.get(picture)
        if file_id is not None:
            return file_id
        return FSInputFile(self._files.path(picture))

    def remember(self, picture: Picture, sent: Message) -> None:
        """Запомнить `file_id` из присланного фото: самый крупный размер — последний."""
        if sent.photo:
            self._file_ids[picture] = sent.photo[-1].file_id
