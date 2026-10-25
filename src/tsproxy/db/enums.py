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
    GRACE_REMINDER = "grace_reminder"  # ежедневное напоминание в льготный период (задача 11)
    GRACE_LAST_DAY = "grace_last_day"  # «последний день» льготного периода (задача 11)
    GRACE_ENDED = "grace_ended"  # льготный кончился, доступ выключен (задача 11)
    TRIAL_EXTENDED = "trial_extended"  # пробный период продлён
    FAREWELL = "farewell"  # прощание после конца пробного
    TRIAL_TRAFFIC_WARNING = "trial_traffic_warning"  # израсходовано 80% пробного трафика
    TRIAL_TRAFFIC_USED_UP = "trial_traffic_used_up"  # пробный трафик кончился


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
