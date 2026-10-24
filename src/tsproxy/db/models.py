"""Таблицы базы бота (схема утверждена владельцем в задаче 03, 2026-10-24).

Срок и статус подписки здесь не хранятся — их знает только Remnawave.
Телефоны, почты, данные карт, сами файлы и ссылки подписки не храним.
"""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from tsproxy.db.enums import (
    MessageAuthor,
    NotificationKind,
    OrderStatus,
    RefundStatus,
    TicketStatus,
)
from tsproxy.db.types import UTCDateTime

# Деньги: до 99 999 999,99 ₽ с копейками
MONEY_PRECISION = 10
MONEY_SCALE = 2

# Длина строк. SQLite её не проверяет, PostgreSQL — проверяет.
ENUM_LENGTH = 32  # с запасом, чтобы новое значение статуса не требовало миграции
NAME_LENGTH = 256
USERNAME_LENGTH = 64  # в Telegram @username до 32 символов
UUID_LENGTH = 64
TARIFF_ID_LENGTH = 32
PROVIDER_LENGTH = 32
PAYMENT_ID_LENGTH = 128
URL_LENGTH = 2048
EVENT_KIND_LENGTH = 64
GLUE_KEY_LENGTH = 128
FILE_ID_LENGTH = 256

# Имена ограничений и индексов одинаковые на SQLite и PostgreSQL —
# без них Alembic не сможет менять таблицы SQLite (batch-режим).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utc_now() -> datetime:
    """Текущее время в UTC — значение по умолчанию для отметок времени."""
    return datetime.now(UTC)


def new_idempotency_key() -> str:
    """Ключ от двойных платежей: отправляется платёжке при создании платежа."""
    return uuid4().hex


def enum_column[E: StrEnum](enum_cls: type[E]) -> Enum:
    """Статус хранится строкой (`VARCHAR`), а не типом ENUM базы — так переносимо."""
    return Enum(
        enum_cls,
        native_enum=False,
        length=ENUM_LENGTH,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {  # noqa: RUF012  # так требует SQLAlchemy
        datetime: UTCDateTime(),
        Decimal: Numeric(MONEY_PRECISION, MONEY_SCALE, asdecimal=True),
        dict[str, Any]: JSON(),
    }


class Client(Base):
    """Клиент бота. Пробный период — поля здесь же: строка одна на Telegram ID,
    поэтому второй пробный выдать некуда (см. `repository.claim_trial`)."""

    __tablename__ = "clients"
    __table_args__ = (
        CheckConstraint("trial_extensions >= 0", name="trial_extensions_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    name: Mapped[str | None] = mapped_column(String(NAME_LENGTH))
    username: Mapped[str | None] = mapped_column(String(USERNAME_LENGTH))
    first_seen_at: Mapped[datetime] = mapped_column(default=utc_now)
    trial_granted_at: Mapped[datetime | None]
    trial_extensions: Mapped[int] = mapped_column(default=0)
    grace_started_at: Mapped[datetime | None]  # пусто — льготный период не идёт
    bot_blocked_at: Mapped[datetime | None]  # пусто — бот не заблокирован


class Order(Base):
    """Заказ тарифа. Цена и дни копируются из тарифа на момент заказа."""

    __tablename__ = "orders"
    __table_args__ = (
        # одна оплата у платёжки засчитывается только одному заказу и только один раз
        UniqueConstraint("payment_provider", "payment_id"),
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint("days > 0", name="days_positive"),
        CheckConstraint("grant_attempts >= 0", name="grant_attempts_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    tariff_id: Mapped[str] = mapped_column(String(TARIFF_ID_LENGTH))
    price: Mapped[Decimal]
    days: Mapped[int]
    status: Mapped[OrderStatus] = mapped_column(
        enum_column(OrderStatus), default=OrderStatus.CREATED, index=True
    )
    created_at: Mapped[datetime] = mapped_column(default=utc_now)
    payment_provider: Mapped[str | None] = mapped_column(String(PROVIDER_LENGTH))
    payment_id: Mapped[str | None] = mapped_column(String(PAYMENT_ID_LENGTH))
    payment_url: Mapped[str | None] = mapped_column(String(URL_LENGTH))
    payment_url_expires_at: Mapped[datetime | None]
    idempotency_key: Mapped[str] = mapped_column(
        String(UUID_LENGTH), unique=True, default=new_idempotency_key
    )
    paid_amount: Mapped[Decimal | None]
    paid_at: Mapped[datetime | None]
    grant_target_expire_at: Mapped[datetime | None]  # «до какой даты продлить»
    grant_attempts: Mapped[int] = mapped_column(default=0)
    next_grant_attempt_at: Mapped[datetime | None] = mapped_column(index=True)


class Notification(Base):
    """Отправленное клиенту уведомление. Одно и то же к одному сроку — только раз. 90 дней."""

    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("client_id", "kind", "related_expire_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"))
    kind: Mapped[NotificationKind] = mapped_column(enum_column(NotificationKind))
    related_expire_at: Mapped[datetime]  # к какому сроку окончания относится
    sent_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)


class Event(Base):
    """Журнал событий: важные события по клиентам, заказам и действиям администраторов.
    Бессрочно. Одинаковые события (например, поддельные уведомления) склеиваются:
    первая запись + счётчик `repeat_count` и время последнего `last_seen_at`."""

    __tablename__ = "events"
    __table_args__ = (CheckConstraint("repeat_count >= 1", name="repeat_count_positive"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)
    kind: Mapped[str] = mapped_column(String(EVENT_KIND_LENGTH), index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id"), index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), index=True)
    admin_id: Mapped[int | None] = mapped_column(BigInteger)  # Telegram ID; пусто — система
    details: Mapped[dict[str, Any] | None]
    # по этому ключу находим запись, к которой склеиваем повтор
    glue_key: Mapped[str | None] = mapped_column(String(GLUE_KEY_LENGTH), index=True)
    repeat_count: Mapped[int] = mapped_column(default=1)
    last_seen_at: Mapped[datetime | None]


class SupportTicket(Base):
    """Обращение в поддержку: одна тема в группе администраторов."""

    __tablename__ = "support_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    topic_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)  # тема в группе
    status: Mapped[TicketStatus] = mapped_column(
        enum_column(TicketStatus), default=TicketStatus.OPEN, index=True
    )
    assignee_admin_id: Mapped[int | None] = mapped_column(BigInteger)  # кто взял («🙋 Беру»)
    opened_at: Mapped[datetime] = mapped_column(default=utc_now)
    first_response_at: Mapped[datetime | None]
    closed_at: Mapped[datetime | None]


class TicketMessage(Base):
    """Сообщение в переписке обращения. Храним только `file_id`, не сами файлы. 3 года."""

    __tablename__ = "ticket_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(ForeignKey("support_tickets.id"), index=True)
    author: Mapped[MessageAuthor] = mapped_column(enum_column(MessageAuthor))
    text: Mapped[str | None] = mapped_column(Text)
    file_id: Mapped[str | None] = mapped_column(String(FILE_ID_LENGTH))
    file_kind: Mapped[str | None] = mapped_column(String(ENUM_LENGTH))  # photo, voice, …
    # номера сообщений в Telegram: у клиента и в теме группы — чтобы ответ ушёл куда нужно
    client_message_id: Mapped[int | None] = mapped_column(BigInteger)
    group_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, index=True)


class RefundRequest(Base):
    """Запрос на возврат за неиспользованные дни. Решает администратор."""

    __tablename__ = "refund_requests"
    __table_args__ = (
        CheckConstraint("unused_days >= 0", name="unused_days_non_negative"),
        CheckConstraint("amount >= 0", name="amount_non_negative"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id"), index=True)
    requested_at: Mapped[datetime] = mapped_column(default=utc_now)
    unused_days: Mapped[int]
    amount: Mapped[Decimal]
    status: Mapped[RefundStatus] = mapped_column(
        enum_column(RefundStatus), default=RefundStatus.REQUESTED, index=True
    )
    decided_by_admin_id: Mapped[int | None] = mapped_column(BigInteger)
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None]


class NodeOutage(Base):
    """Сбой сервера (ноды). Компенсация начисляется один раз на сбой
    (см. `repository.claim_outage_compensation`)."""

    __tablename__ = "node_outages"
    # один и тот же сбой не записывается дважды (повторный вебхук)
    __table_args__ = (UniqueConstraint("node_uuid", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    node_uuid: Mapped[str] = mapped_column(String(UUID_LENGTH))  # какой сервер в Remnawave
    started_at: Mapped[datetime]
    ended_at: Mapped[datetime | None]
    compensated_at: Mapped[datetime | None]  # пусто — не компенсировано
    compensated_by_admin_id: Mapped[int | None] = mapped_column(BigInteger)
