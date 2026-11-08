"""Свои типы колонок: время в UTC с часовым поясом на любой базе."""

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


class UTCDateTime(TypeDecorator[datetime]):
    """Время всегда в UTC и всегда с часовым поясом.

    SQLite не умеет хранить часовой пояс: пишем туда время в UTC без пояса,
    а при чтении приклеиваем UTC обратно. PostgreSQL хранит `timestamptz` сам.
    Время без пояса на запись не принимаем — это почти всегда ошибка (`datetime.now()`).
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"Время без часового пояса нельзя записать в базу: {value!r}")
        utc_value = value.astimezone(UTC)
        if dialect.name == "sqlite":
            return utc_value.replace(tzinfo=None)
        return utc_value

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
