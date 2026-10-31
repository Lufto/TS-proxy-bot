"""Журнал событий: одна точка записи важных событий (задача 18, решение 13 задачи 21).

Запись идёт в сессии вызывающего кода и **не** фиксируется здесь: событие сохраняется
одной транзакцией с самим изменением (оплата засчитана и событие «оплата получена» —
либо оба, либо ничего). `commit` делает вызывающий код.

Склейка. Если передан `glue_key` и в базе есть событие того же вида с тем же ключом,
впервые записанное меньше чем `glue_window` назад, новая строка не создаётся:
у найденной строки `repeat_count` растёт на 1, а `last_seen_at` становится текущим временем.
Окно отсчитывается от первой записи (`occurred_at`), а не от последнего повтора, поэтому
даже бесконечный поток даёт новую строку раз в окно — видно, когда поток шёл.
Клиент, заказ и подробности остаются от первой записи.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tsproxy.db.models import GLUE_KEY_LENGTH, Event
from tsproxy.services.clock import Clock, utc_now
from tsproxy.services.masking import Masker, sanitize_details

DEFAULT_EVENT_GLUE_WINDOW = timedelta(hours=1)


class EventKind(StrEnum):
    """Виды важных событий (список — задача 18). В базе хранится строковое значение."""

    ORDER_CREATED = "order_created"  # заказ создан
    PAYMENT_CREATED = "payment_created"  # платёж создан, есть ссылка на оплату
    PAYMENT_RECEIVED = "payment_received"  # оплата получена и засчитана
    ACCESS_GRANTED = "access_granted"  # доступ выдан (подписка продлена в Remnawave)
    ACCESS_FAILED = "access_failed"  # доступ не выдан
    TRIAL_GRANTED = "trial_granted"  # пробный период выдан
    TRIAL_EXTENDED = "trial_extended"  # пробный период продлён
    GRACE_STARTED = "grace_started"  # льготный период начался
    GRACE_ENDED = "grace_ended"  # льготный период закончился
    # Задача 21: возврат решён (администратором или клиентом — случайная вторая оплата)
    REFUND_APPROVED = "refund_approved"
    REFUND_IN_REVIEW = "refund_in_review"  # платёжка отправила возврат на проверку
    REFUND_COMPLETED = "refund_completed"  # платёжка вернула деньги (по нему — /stats)
    REFUND_FAILED = "refund_failed"  # платёжка отказала в возврате
    # срок «до X» после возврата — посчитан и записан до запроса к панели
    REFUND_TERM_PLANNED = "refund_term_planned"
    REFUND_TERM_SHORTENED = "refund_term_shortened"  # срок укорочен после возврата
    # клиент нажал «Оставить» под сообщением о двойной оплате — вернуть только через поддержку
    DOUBLE_PAYMENT_KEPT = "double_payment_kept"
    COMPENSATION_GRANTED = "compensation_granted"  # компенсация за сбой
    ADMIN_ACTION = "admin_action"  # любое действие администратора
    FAKE_PAYMENT_NOTIFICATION = "fake_payment_notification"  # отклонённое поддельное уведомление
    # Ссылку подписки перевыпустил сам клиент (задача 31), кто — в `details["by"]`.
    # Перевыпуск администратором, как и любое его действие, — ADMIN_ACTION (задача 18)
    SUBSCRIPTION_REVOKED = "subscription_revoked"
    # задача 28: выключатель пробных. Последнее из двух — источник правды, вкл или выкл
    TRIALS_DISABLED = "trials_disabled"  # пробные выключены (администратором или при спаме)
    TRIALS_ENABLED = "trials_enabled"  # пробные снова включены администратором
    # задача 33: оплата звёздами пришла, а засчитать её некуда; вернули ли звёзды — `returned`
    STARS_PAYMENT_RETURNED = "stars_payment_returned"


class EventLog:
    """Записывает события в таблицу `events`, склеивая повторы по `glue_key`."""

    def __init__(
        self,
        clock: Clock,
        mask: Masker,
        glue_window: timedelta = DEFAULT_EVENT_GLUE_WINDOW,
    ) -> None:
        if glue_window <= timedelta(0):
            raise ValueError("Окно склейки событий должно быть больше нуля")
        self._clock = clock
        self._mask = mask
        self._glue_window = glue_window

    async def record(
        self,
        session: AsyncSession,
        kind: EventKind,
        *,
        client_id: int | None = None,
        order_id: int | None = None,
        admin_id: int | None = None,
        details: Mapping[str, Any] | None = None,
        glue_key: str | None = None,
    ) -> Event:
        """Записать событие (или приклеить повтор). Возвращает строку журнала.

        `details` очищаются от секретов и полных ссылок подписки до записи.
        `admin_id` — Telegram ID администратора; пусто — действие системы.
        """
        now = utc_now(self._clock)
        key = self._checked_glue_key(glue_key)
        if key is not None:
            glued = await self._glue(session, kind, key, now)
            if glued is not None:
                return glued
        event = Event(
            occurred_at=now,
            kind=kind.value,
            client_id=client_id,
            order_id=order_id,
            admin_id=admin_id,
            details=sanitize_details(details, self._mask) if details is not None else None,
            glue_key=key,
        )
        session.add(event)
        await session.flush()
        return event

    def _checked_glue_key(self, glue_key: str | None) -> str | None:
        if glue_key is None:
            return None
        key = self._mask(glue_key)
        if not key or len(key) > GLUE_KEY_LENGTH:
            raise ValueError(f"Ключ склейки должен быть от 1 до {GLUE_KEY_LENGTH} символов")
        return key

    async def _glue(
        self, session: AsyncSession, kind: EventKind, key: str, now: datetime
    ) -> Event | None:
        """Приклеить повтор к свежему событию с тем же ключом. `None` — приклеить не к чему."""
        target_id = await session.scalar(
            select(Event.id)
            .where(
                Event.kind == kind.value,
                Event.glue_key == key,
                Event.occurred_at > now - self._glue_window,
            )
            .order_by(Event.occurred_at.desc(), Event.id.desc())
            .limit(1)
        )
        if target_id is None:
            return None
        # счётчик растёт в самой базе: два одновременных повтора не потеряют ни одного
        return await session.scalar(
            update(Event)
            .where(Event.id == target_id)
            .values(repeat_count=Event.repeat_count + 1, last_seen_at=now)
            .returning(Event),
            execution_options={"populate_existing": True},
        )
