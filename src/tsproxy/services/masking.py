"""Очистка того, что попадает в журнал событий и в тревоги: без секретов и полных ссылок подписки.

Сама маскировка строк — в `logging_setup.MaskingFilter.mask` (секреты из `.env` и ссылки
подписки). Сервисы получают её снаружи как `Masker`, здесь — обход вложенных подробностей.
"""

from collections.abc import Callable, Mapping
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from tsproxy.logging_setup import MASK

type Masker = Callable[[str], str]

# Значение поля с такой частью в имени не сохраняем вовсе, даже если маскировщик его не узнал
SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "token",
    "secret",
    "password",
    "authorization",
    "api_key",
    "card",
    "subscription_url",
)

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def sanitize_details(details: Mapping[str, Any], mask: Masker) -> dict[str, JsonValue]:
    """Подробности события → словарь для JSON-колонки, со скрытыми секретами.

    Строки проходят через маскировщик, поля с «опасными» именами заменяются на `****`,
    деньги (`Decimal`) и время превращаются в строки. Незнакомый тип — ошибка, а не молчаливое
    `str()`: так в журнал случайно не попадёт целый объект со всеми полями.
    """
    return {
        mask(str(key)): MASK if is_sensitive_key(str(key)) else _sanitize_value(value, mask)
        for key, value in details.items()
    }


def _sanitize_value(value: object, mask: Masker) -> JsonValue:
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, Enum):
        return _sanitize_value(value.value, mask)
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return sanitize_details(value, mask)
    if isinstance(value, list | tuple | set | frozenset):
        return [_sanitize_value(item, mask) for item in value]
    raise TypeError(f"Тип {type(value).__name__} нельзя записать в подробности события")
