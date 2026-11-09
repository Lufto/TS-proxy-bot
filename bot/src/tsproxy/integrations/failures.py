"""Вид сбоя внешнего сервиса (Remnawave, позже Platega и Telegram): что именно не вышло.

По виду сбоя тревога берёт из `alerts/` «что случилось» и причины, куда смотреть
(`failure.<вид>`). Подробности (HTTP-код, сколько ждали, сколько попыток) подставляются в текст.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import ClassVar


@dataclass(frozen=True)
class Failure:
    """Вид сбоя (`remnawave.timeout`) и подстановки для его текста `failure.<вид>.what`."""

    kind: str
    details: Mapping[str, str | int] = field(default_factory=dict)


class ServiceError(Exception):
    """Ошибка внешнего сервиса. Вид сбоя — из перевода ошибки библиотеки, иначе общий
    вид класса (`default_kind`), а у ошибок вроде «аккаунта нет» его нет вовсе."""

    default_kind: ClassVar[str | None] = None

    def __init__(self, message: str, *, failure: Failure | None = None) -> None:
        super().__init__(message)
        if failure is None and self.default_kind is not None:
            failure = Failure(self.default_kind)
        self.failure = failure
