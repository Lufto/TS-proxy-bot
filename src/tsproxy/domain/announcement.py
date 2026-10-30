"""Объявление клиентам в приложении (задача 35): правила текста и запись в заголовок.

Happ и v2RayTun показывают объявление из заголовка ответа подписки `announce`. Значение —
обычный текст или `base64:` и текст в UTF-8, закодированный base64 (документация Happ,
dev-docs → «Announcement»; документация v2RayTun → «announce»). Обычным текстом русские
буквы в заголовок HTTP не положить, поэтому пишем всегда в base64.

Happ показывает не больше 200 символов. Длину считаем в единицах UTF-16 (эмодзи — два
символа): так считают приложения на телефонах, и текст точно не обрежется.
"""

import base64
from enum import StrEnum

ANNOUNCE_MAX_LENGTH = 200
BASE64_PREFIX = "base64:"
UTF16_UNIT_BYTES = 2
UTF16 = "utf-16-le"


class AnnouncementProblem(StrEnum):
    """Почему текст нельзя поставить объявлением."""

    EMPTY = "empty"
    TOO_LONG = "too_long"  # длиннее, чем показывает Happ


def announcement_length(text: str) -> int:
    """Длина объявления, как её посчитает приложение: без пробелов по краям, в UTF-16."""
    return len(text.strip().encode(UTF16)) // UTF16_UNIT_BYTES


def announcement_problem(text: str) -> AnnouncementProblem | None:
    """Что не так с текстом объявления. `None` — можно ставить."""
    length = announcement_length(text)
    if length == 0:
        return AnnouncementProblem.EMPTY
    if length > ANNOUNCE_MAX_LENGTH:
        return AnnouncementProblem.TOO_LONG
    return None


def encode_announcement(text: str) -> str:
    """Значение заголовка `announce`: `base64:` и текст без пробелов по краям."""
    problem = announcement_problem(text)
    if problem is not None:
        raise ValueError(f"Текст объявления не подходит: {problem.value}")
    encoded = base64.b64encode(text.strip().encode("utf-8")).decode("ascii")
    return BASE64_PREFIX + encoded


def decode_announcement(value: str) -> str:
    """Текст объявления из заголовка. Обычный текст (поставлен в панели руками) — как есть."""
    if not value.startswith(BASE64_PREFIX):
        return value
    try:
        raw = base64.b64decode(value.removeprefix(BASE64_PREFIX), validate=True)
        return raw.decode("utf-8")
    except ValueError:
        # Не base64 (в том числе не латиница) или не UTF-8 — показываем как есть
        return value
