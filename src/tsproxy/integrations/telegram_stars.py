"""Telegram Stars — второй способ оплаты (задача 33): возврат звёзд (`refundStarPayment`).

Счёт (`sendInvoice`) и ответы на `pre_checkout_query` / `successful_payment` — в боте
(`bot.stars`), правила — в `domain.stars`, зачёт — `services.stars`.

Telegram возвращает звёзды по паре «клиент + номер платежа» (`telegram_payment_charge_id`)
и сразу: возврат либо прошёл, либо отказ. Ключа идемпотентности у Telegram нет, поэтому
повтор после сбоя («звёзды уже возвращены», `CHARGE_ALREADY_REFUNDED`) считаем успехом:
одну оплату бот начинает возвращать только один раз (`services.refunds`).
"""

import logging
from decimal import Decimal

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

from tsproxy.integrations.payments_types import (
    PaidPayment,
    PaymentRejected,
    PaymentUnavailable,
    Refund,
    RefundOutcome,
)

logger = logging.getLogger(__name__)

# Имя способа оплаты в заказе (`orders.payment_provider`)
STARS_PROVIDER = "telegram_stars"
# Ответ Telegram на повторный возврат того же платежа
ALREADY_REFUNDED = "CHARGE_ALREADY_REFUNDED"
# Ошибки, после которых повтор может помочь: сеть, 5xx, «слишком часто»
RETRYABLE_ERRORS = (TelegramNetworkError, TelegramServerError, TelegramRetryAfter)


class TelegramStarsRefunds:
    """Возврат оплаты звёздами (`services.refunds` через `RefundRouter`)."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def refund(self, payment: PaidPayment, amount: Decimal, idempotency_key: str) -> Refund:
        """Вернуть звёзды целиком. `amount` — рубли заказа, только для записи в журнал."""
        charge_id = payment.payment_id
        try:
            await self._bot.refund_star_payment(
                user_id=payment.telegram_id, telegram_payment_charge_id=charge_id
            )
        except TelegramBadRequest as error:
            if ALREADY_REFUNDED not in error.message:
                raise PaymentRejected(f"Telegram отказал в возврате: {error.message}") from error
            logger.warning("Звёзды по платежу %s уже возвращены (%s)", charge_id, idempotency_key)
        except RETRYABLE_ERRORS as error:
            raise PaymentUnavailable(f"Telegram не ответил на возврат: {error}") from error
        except TelegramAPIError as error:
            raise PaymentRejected(f"Telegram отказал в возврате: {error.message}") from error
        logger.info("Звёзды по платежу %s возвращены клиенту %s", charge_id, payment.telegram_id)
        return Refund(charge_id, charge_id, amount, RefundOutcome.SUCCEEDED)
