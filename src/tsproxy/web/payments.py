"""Приём уведомлений платёжки об оплате (задача 14; задача 21 п. 12; аудит §4.1, §4.5).

Уведомления принимаем **от кого угодно, без фильтра по IP**: адреса платёжки могут меняться,
а фильтр отрезал бы настоящие оплаты. Защита — в порядке проверки (`PaymentChecks`):
заказа нет или он уже засчитан — без запроса к платёжке; иначе перепроверка у платёжки.

Ответы:
- 413 — тело больше 64 КБ, не читаем;
- 400 — тело не разобрано (не тот формат, неверная подпись): подделка, склеенная запись
  в журнале и тревога;
- 200 — результат записан (засчитано, не оплачено, повтор, неизвестный платёж);
- 503 — платёжка не ответила на перепроверку, 500 — не записалось в базу: платёжка
  пришлёт уведомление ещё раз, а фоновая проверка найдёт оплату и без него.

Формат тела у каждой платёжки свой — его разбирает сама платёжка (`parse_notification`).
Ответ «200 без тела» подходит поддельной платёжке и ЮKassa; если настоящей платёжке нужен
особый ответ (Robokassa ждёт `OK<номер>`), его добавит задача 15.
"""

import logging
from dataclasses import dataclass

from fastapi import APIRouter, Request, Response, status
from sqlalchemy.exc import SQLAlchemyError

from tsproxy.integrations.payments_types import (
    InvalidNotification,
    PaymentProvider,
    PaymentUnavailable,
)
from tsproxy.services.payment_checks import ForgedReason, PaymentChecks

logger = logging.getLogger(__name__)

PAYMENT_NOTIFICATION_PATH = "/payments/notification"
MAX_BODY_BYTES = 64 * 1024


@dataclass(frozen=True)
class PaymentNotificationWebhook:
    """Приёмник уведомлений платёжки: кто разбирает тело и кто проверяет оплату."""

    payments: PaymentProvider
    checks: PaymentChecks

    def router(self) -> APIRouter:
        router = APIRouter()
        router.add_api_route(
            PAYMENT_NOTIFICATION_PATH, self.receive, methods=["POST"], include_in_schema=False
        )
        return router

    async def receive(self, request: Request) -> Response:
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            logger.warning("Уведомление об оплате: тело больше %d байт — отклонено", MAX_BODY_BYTES)
            return Response(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        body = await request.body()
        if len(body) > MAX_BODY_BYTES:
            logger.warning("Уведомление об оплате: тело больше %d байт — отклонено", MAX_BODY_BYTES)
            return Response(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
        # IP — только справка в журнал подделок: по нему ничего не отбрасываем
        source = None if request.client is None else request.client.host
        try:
            payment_id = self.payments.parse_notification(body, request.headers)
        except InvalidNotification as error:
            logger.warning("Уведомление об оплате не разобрано: %s", error)
            await self.checks.record_forged(ForgedReason.UNPARSED, None, source)
            return Response(status_code=status.HTTP_400_BAD_REQUEST)
        try:
            outcome = await self.checks.handle_notification(payment_id, source)
        except PaymentUnavailable as error:
            logger.warning("Уведомление об оплате %s: платёжка не ответила: %s", payment_id, error)
            return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
        except SQLAlchemyError:
            # зачёт уже поднял тревогу; ошибка ответом — платёжка повторит уведомление
            logger.exception("Уведомление об оплате %s: не записалось в базу", payment_id)
            return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
        logger.info("Уведомление об оплате %s: %s", payment_id, outcome.value)
        return Response(status_code=status.HTTP_200_OK)
