"""Статусы и виды записей в базе.

В базе хранятся строковые значения (`"paid"`), а не имена членов (`PAID`),
поэтому добавление нового значения не требует миграции.
"""

from enum import StrEnum


class OrderStatus(StrEnum):
    """Путь заказа: создан → оплачен → доступ выдан / не выдан / ручная проверка …"""

    CREATED = "created"
    PAID = "paid"
    ACCESS_GRANTED = "access_granted"
    ACCESS_FAILED = "access_failed"
    MANUAL_REVIEW = "manual_review"
    CANCELED = "canceled"
    REFUNDED = "refunded"


class NotificationKind(StrEnum):
    """Какое уведомление отправлено клиенту."""

    DAY_BEFORE = "day_before"  # за день до окончания
    HOUR_BEFORE = "hour_before"  # за час до окончания
    GRACE = "grace"  # начался льготный период
    TRIAL_EXTENDED = "trial_extended"  # пробный период продлён
    FAREWELL = "farewell"  # прощание после конца пробного


class TicketStatus(StrEnum):
    """Состояние обращения в поддержку."""

    OPEN = "open"
    CLOSED = "closed"


class MessageAuthor(StrEnum):
    """Кто написал сообщение в переписке обращения."""

    CLIENT = "client"
    SUPPORT = "support"  # ответ администратора, ушёл клиенту
    NOTE = "note"  # внутренняя заметка `//`, клиенту не уходит


class RefundStatus(StrEnum):
    """Путь запроса на возврат."""

    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    REFUNDED = "refunded"  # платёжка вернула деньги
