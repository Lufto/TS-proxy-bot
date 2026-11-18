"""Картинки экранов клиента — файлы `content/images/` (bot-telegram-ui, задача 04).

Экран с картинкой (`Screen.picture`) приходит фото с подписью. Как картинка попадает
в Telegram (загрузка один раз, дальше по `file_id`) — `bot.pictures.TelegramPictures`.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

PICTURES_FOLDER = "images"
PICTURE_SUFFIX = ".png"


class Picture(StrEnum):
    """Картинка экрана; значение — имя файла без расширения."""

    # Главное меню (задача 27): индикатор цветом статуса подписки
    MENU_ACTIVE = "menu-active"  # на связи: оплаченный срок или пробный
    MENU_GRACE = "menu-grace"  # срок кончился, доступ сохранён (льготный период)
    MENU_EXPIRED = "menu-expired"  # нет доступа
    MENU_NEW = "menu-new"  # не подключено: нет прокси-аккаунта
    MENU_NEUTRAL = "menu-neutral"  # без индикатора: панель не ответила, статус неизвестен
    ABOUT = "about"  # «О сервисе» (задача 28)


@dataclass(frozen=True)
class PictureFiles:
    """Где лежат файлы картинок."""

    directory: Path

    def path(self, picture: Picture) -> Path:
        return self.directory / f"{picture.value}{PICTURE_SUFFIX}"

    def missing(self) -> list[Path]:
        """Файлы, которых нет: без них бот не стартует (`load_content`)."""
        return [self.path(picture) for picture in Picture if not self.path(picture).is_file()]
