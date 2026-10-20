# Детали Cardlink

Type: research
Status: open

## Question

Что нужно знать о Cardlink сверх базовой документации (https://cardlink.link/reference/api), чтобы принять решения по оплате:
- где берутся `shop_id` и токен, как настраиваются Result URL, Success URL и Fail URL;
- поведение статусов `UNDERPAID` и `OVERPAID`, `ttl` счёта, можно ли оплатить просроченный счёт;
- есть ли тестовый режим или песочница;
- точная проверка подписи postback (`md5(OutSum:InvId:apiToken)`, регистр, формат суммы), повторы при не-200 ответе;
- комиссии, минимальная сумма, требования к магазину (оферта, чеки 54-ФЗ).

Результат — в `.scratch/vpn-bot-rework/research/cardlink.md`.
