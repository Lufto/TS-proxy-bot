# Оплата 1: тарифы и заказ на поддельной платёжке

Type: task
Status: resolved
Blocked by: 02, 03, 06

## Что сделать

Интерфейс платёжки (создать / статус / вернуть), `FakePaymentProvider`, режим `PAYMENTS_MODE=fake` (не стартует с боевыми ключами), скрипт `simulate_payment.py`. Экран тарифов (скрытый тариф 15 ₽ — только администраторам) и оплаты, заказ с ценой из файла, переиспользование платежа на 1 час.

## Готово, когда

- Тест: двойное нажатие «Купить» даёт один платёж; другой тариф — новый.

Решения: задача 04 (в `../vpn-bot-rework/issues/`). Спецификация — `../spec.md`.

## Comments

- 2026-10-25, агент: сделано. Интерфейс платёжки — `integrations/payments_types.py` (создать / статус / вернуть, ошибки `PaymentUnavailable`, `PaymentNotFound`, `PaymentRejected`); поддельная — `integrations/fake_payments.py` (книга платежей в памяти или в `FAKE_PAYMENTS_FILE`, общий файл со `scripts/simulate_payment.py`). `PAYMENTS_MODE=fake` с `RUN_MODE=prod` — ошибка настроек; `PAYMENTS_MODE=real` пока не стартует («задача 15»). Заказ и платёж — `services/orders.py`: цена из `tariffs.yaml`, скрытый тариф только `ADMIN_IDS` (проверка в сервисе), та же ссылка на тот же тариф, пока ей жить ≥ 5 минут, блокировка на клиента (`services/locks.py`), не больше 5 новых платежей за 10 минут; платёжка не ответила — повтор с тем же ключом идемпотентности. Экраны — `bot/tariffs.py` («💳 Тарифы», `/buy`, экран оплаты). Тест «двойное нажатие — один платёж, другой тариф — новый»: `tests/services/test_orders.py`, `tests/bot/test_tariffs.py`. Схема базы не менялась.
