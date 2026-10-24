"""Ручная проверка оплаты на Mac: «оплатить» или «отменить» платёж поддельной платёжки.

Работает только с `PAYMENTS_MODE=fake` и `RUN_MODE=dev`. Меняет файл платежей
(`FAKE_PAYMENTS_FILE`), который читает бот; настоящих денег и платёжек не касается.

    uv run python scripts/simulate_payment.py --list
    uv run python scripts/simulate_payment.py --order 12                 # оплатить по тарифу
    uv run python scripts/simulate_payment.py --order 12 --amount 100    # не на сумму тарифа
    uv run python scripts/simulate_payment.py --order 12 --cancel        # клиент не стал платить

Бот узнаёт об оплате, когда спрашивает статус платежа: кнопка «Проверить оплату» и фоновая
проверка — задачи 13–14. Код выхода 0 — сделано; 1 — платёж не найден или его нельзя изменить.
"""

import argparse
import logging
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from tsproxy.config import PaymentsMode, RunMode, load_settings
from tsproxy.integrations.fake_payments import FakePayment, FakePaymentLedger
from tsproxy.integrations.payments_types import PaymentError, PaymentStatus
from tsproxy.logging_setup import setup_logging

logger = logging.getLogger("simulate_payment")


def _amount(raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"{raw!r} — не число") from None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Оплатить или отменить поддельный платёж")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--list", action="store_true", help="показать все платежи")
    target.add_argument("--order", type=int, help="номер заказа (последний его платёж)")
    target.add_argument("--payment", help="id платежа fake_…")
    parser.add_argument("--amount", type=_amount, help="оплатить эту сумму, а не цену тарифа")
    parser.add_argument("--cancel", action="store_true", help="отменить вместо оплаты")
    return parser.parse_args()


def _describe(payment: FakePayment) -> str:
    return (
        f"заказ {payment.order_id}, платёж {payment.payment_id}: {payment.amount} ₽, "
        f"{payment.status.value}, создан {payment.created_at:%d.%m.%Y %H:%M} UTC"
    )


def _target(ledger: FakePaymentLedger, args: argparse.Namespace) -> FakePayment | None:
    if args.payment is not None:
        return next((p for p in ledger.all() if p.payment_id == args.payment), None)
    payments = ledger.find_by_order(args.order)
    pending = [p for p in payments if p.status is PaymentStatus.PENDING]
    return (pending or payments or [None])[-1]


def main() -> int:
    settings = load_settings()
    setup_logging(settings.log_level, settings.secret_values())
    if settings.payments_mode is not PaymentsMode.FAKE or settings.run_mode is not RunMode.DEV:
        logger.error("Только для PAYMENTS_MODE=fake и RUN_MODE=dev")
        return 1
    args = _parse_args()
    ledger = FakePaymentLedger(settings.fake_payments_file)
    if args.list:
        for payment in ledger.all():
            logger.info(_describe(payment))
        return 0
    payment = _target(ledger, args)
    if payment is None:
        logger.error("Платёж не найден")
        return 1
    try:
        if args.cancel:
            changed = ledger.cancel(payment.payment_id)
        else:
            changed = ledger.pay(payment.payment_id, now=datetime.now(UTC), amount=args.amount)
    except PaymentError as error:
        logger.error("Не получилось: %s", error)
        return 1
    logger.info("Готово — %s", _describe(changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
