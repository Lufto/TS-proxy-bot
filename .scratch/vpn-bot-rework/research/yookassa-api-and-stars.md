# API ЮKassa и Telegram Stars: факты для модуля оплаты TS Proxy

Ответ на тикет `issues/19-api-yookassa-i-telegram-stars.md`. Проверено 2026-10-21 по первоисточникам: документации ЮKassa (yookassa.ru/developers и yookassa.ru/docs/support), OpenAPI-спецификации ЮKassa, исходникам SDK `yookassa` 3.13.0 с PyPI, документации Telegram Bot API, условиям Telegram и Fragment. Аутентифицированных запросов не делалось.

TS Proxy — прокси-сервис с шифрованием трафика, продаётся через Telegram-бота. Оплата: ЮKassa (СБП и карты), владелец — самозанятый; Telegram Stars — дополнительный способ.

Обозначения:
- **[факт]** — прямо написано в первоисточнике (ссылка рядом);
- **[вывод]** — мой вывод из фактов, в источнике прямо не сказано;
- **[не документировано]** — в первоисточниках не нашёл, нужно спросить поддержку или проверить в тестовом магазине.

---

## ⚠️ Главное

1. **ЮKassa не отправляет чеки самозанятого в «Мой налог».** Документация: «Если вы самозанятый, то при приеме оплаты вам нужно регистрировать свой доход в сервисе Мой налог и передавать сформированный чек покупателю… Вы можете делать это вручную. **В ЮKassa эта опция недоступна.**» ([receipts/basics](https://yookassa.ru/developers/payment-acceptance/receipts/basics)). Это противоречит строке «чеки в «Мой налог» формируются автоматически» в `research/yookassa.md` — та строка неверна. [факт]
2. **Деньги самозанятому приходят в течение двух рабочих дней**, а не на следующий день. «На следующий рабочий день» в `yookassa.md` относится к компаниям ([страница для самозанятых](https://yookassa.ru/platezhi-dlya-samozanyatyh/), [conditions](https://yookassa.ru/docs/support/payments/conditions)). [факт]
3. **Ограничения для самозанятых:** платить могут только физлица; с карт можно принять до 500 000 ₽ в месяц; годовой доход по закону не выше 2,4 млн ₽ ([страница для самозанятых](https://yookassa.ru/platezhi-dlya-samozanyatyh/)). [факт]
4. **СБП нельзя проверить в тестовом магазине.** «You can test payments via bank cards and YooMoney wallets. Other payment methods can only be tested in a real store.» ([testing](https://yookassa.ru/developers/payment-acceptance/testing-and-going-live/testing)). [факт]
5. **Список IP в SDK отличается от документации.** SDK 3.13.0 проверяет 4 подсети IPv6 `/64`, а документация разрешает всю `2a02:5180::/32`. Сверять нужно с документацией (список ниже). [факт]
6. **Правило Telegram о цифровых товарах в силе.** Цифровые товары и услуги внутри Telegram-приложений продаются только за Stars, даже если у продавца есть своя внешняя оплата. Если бот использует другую оплату, Telegram присылает предупреждение. Если его не выполнить, бот могут скрыть из версий Telegram в магазинах приложений или удалить ([Bot Developer ToS §6.2](https://telegram.org/tos/bot-developers), [payments-stars FAQ](https://core.telegram.org/bots/payments-stars)). [факт] Продажа TS Proxy по ссылке на ЮKassa подпадает под это правило. [вывод]
7. **Stars выводятся только криптовалютой через Fragment:** на TON-кошелёк, в токене Gram (бывший Toncoin), минимум 1000 Stars, Stars становятся доступны через срок до 21 дня. Про проверку личности (KYC) и про граждан РФ первоисточники ничего не говорят. Telegram и Fragment оставляют за собой право не выплачивать в отдельных странах. [факт + не документировано]

---

## Часть 1. ЮKassa (API v3)

### 1.1. Создание платежа для бота

**Запрос** [факт] ([payment-process](https://yookassa.ru/developers/payment-acceptance/getting-started/payment-process), [interaction-format](https://yookassa.ru/developers/using-api/interaction-format), [OpenAPI](https://yookassa.ru/developers/api/yookassa-openapi-specification.yaml)):

- `POST https://api.yookassa.ru/v3/payments`, HTTP Basic Auth: логин — `shopId`, пароль — секретный ключ.
- Заголовок `Idempotence-Key` обязателен для POST и DELETE. До 64 символов, шаблон `[0-9a-zA-Z+-_.]{1,64}`. Спецификация рекомендует UUID v7, страница interaction-format — UUID v4.
- Ключ идемпотентности действует **24 часа** с первого запроса. Тот же ключ и те же данные — ЮKassa считает запрос повтором и возвращает результат первого. Другой ключ с теми же данными — новый платёж. Запросы через 24 часа обрабатываются как новые.
- `capture: true` — платёж в одну стадию, после оплаты сразу `succeeded`. **По умолчанию `capture` = `false`** (двухстадийный платёж), поэтому `true` нужно передавать явно. Для СБП двухстадийных платежей нет вообще ([sbp](https://yookassa.ru/developers/payment-acceptance/integration-scenarios/manual-integration/other/sbp)).
- `confirmation: {"type": "redirect", "return_url": "..."}`. В ответе приходит `confirmation.confirmation_url`, на него отправляем пользователя.
- `metadata` — ключи и значения произвольные, ЮKassa возвращает их без изменений, в том числе в уведомлениях. Ограничения: **до 16 ключей, ключ до 32 символов, значение до 512 символов, только строки UTF-8**. Сюда кладём `order_id`.
- `description` — до 128 символов, пользователь видит его при оплате.
- `payment_method_data: {"type": "sbp"}` или `{"type": "bank_card"}` — способ оплаты выбран заранее. Если не передавать, пользователь выберет способ на странице ЮKassa ([quick-start](https://yookassa.ru/developers/payment-acceptance/getting-started/quick-start)). [факт]
- Если ответ не готов за 30 секунд, ЮKassa возвращает HTTP 500 или пытается отменить платёж. **HTTP 500 не значит, что платёж не прошёл**: сначала нужно узнать результат, потом решать ([interaction-format](https://yookassa.ru/developers/using-api/interaction-format)). [факт] Поэтому при 500 или таймауте повторяем запрос **с тем же ключом идемпотентности**. [вывод]

**Способы оплаты** [факт]:

| | СБП (`sbp`) | Карта (`bank_card`) |
|---|---|---|
| Подтверждение | Redirect | Redirect |
| Срок на оплату | 1 час | 1 час |
| Двухстадийный платёж (холд) | нет | 7 дней |
| Возврат | полный и частичный, на следующий день | полный и частичный, 0–3 дня |
| Автоплатежи | да, но не во всех банках | да |
| Лимит на платёж | 1–700 000 ₽ | 1–350 000 ₽ |

Источники: [sbp](https://yookassa.ru/developers/payment-acceptance/integration-scenarios/manual-integration/other/sbp), [bank-card](https://yookassa.ru/developers/payment-acceptance/integration-scenarios/manual-integration/bank-card), [payment-methods](https://yookassa.ru/developers/payment-acceptance/getting-started/payment-methods). Для самозанятого действует ещё лимит по картам 500 000 ₽ в месяц.

**`return_url` в Telegram-бота:**
- В спецификации: «URL, на который вернется пользователь после подтверждения или отмены платежа на веб-странице. Не более 2048 символов». Других ограничений на схему или домен там нет. [факт]
- Ссылка вида `https://t.me/<bot_username>?start=<param>` — обычный HTTPS-URL, поэтому подходит. [вывод] Работает ли схема `tg://` — [не документировано].
- После возврата пользователя ЮKassa советует запросить статус платежа ([bank-card](https://yookassa.ru/developers/payment-acceptance/integration-scenarios/manual-integration/bank-card)). [факт] Возврат по `return_url` сам по себе не доказывает оплату. [вывод]

### 1.2. Сколько живёт неоплаченный платёж и можно ли переиспользовать ссылку

- **[факт]** Для СБП и карт срок на оплату — **1 час**. Если пользователь не подтвердил платёж за это время, ЮKassa **сама отменяет его**: статус `canceled`, причина `cancellation_details.reason = expired_on_confirmation`. «To allow the user to pay, you need to create a new payment» ([payment-methods → Payment term](https://yookassa.ru/developers/payment-acceptance/getting-started/payment-methods), [declined-payments](https://yookassa.ru/developers/payment-acceptance/after-the-payment/declined-payments)).
- **[факт]** Статус `canceled` окончательный. Чтобы повторить оплату, нужен новый платёж **с новым ключом идемпотентности** ([declined-payments](https://yookassa.ru/developers/payment-acceptance/after-the-payment/declined-payments)).
- **[факт]** Поле `expires_at` в объекте платежа относится к сроку списания для `waiting_for_capture`, а не к сроку `pending`. Отдельного поля со сроком жизни `pending` в объекте платежа нет ([OpenAPI](https://yookassa.ru/developers/api/yookassa-openapi-specification.yaml)).
- **[факт]** Банковское приложение может провести оплату после того, как срок в ЮKassa уже истёк. Тогда деньги спишутся, но платёж не пройдёт, и деньги «instantly returned to the user» ([payment-methods](https://yookassa.ru/developers/payment-acceptance/getting-started/payment-methods)).
- **[вывод]** Ссылку `confirmation_url` можно снова показывать тому же пользователю для того же тарифа, пока платёж в `pending` и не прошёл час с момента создания. Перед повторной выдачей стоит запросить `GET /v3/payments/{id}`: если там `canceled` или времени почти не осталось, создать новый платёж. Платёж нельзя оплатить дважды, потому что `succeeded` и `canceled` окончательны.
- **[не документировано]** От какого момента отсчитывается час, если способ оплаты не передан в `payment_method_data` и пользователь ещё не выбрал его на странице. Считать от `created_at` — осторожная оценка. [вывод]
- **[вывод]** Если за 24 часа повторить `POST /v3/payments` с тем же ключом и тем же телом, вернётся тот же платёж. На этом можно построить переиспользование ссылки для одного заказа. Но через час платёж будет `canceled`, поэтому после часа нужен новый ключ.

### 1.3. Уведомления (HTTP notifications)

[факт] ([webhooks](https://yookassa.ru/developers/using-api/webhooks)):

- **Настройка.** Для Basic Auth уведомления настраиваются в личном кабинете: раздел «Интеграция — HTTP-уведомления», там указывается URL и события. Через API (`/v3/webhooks`) подписываться можно только по OAuth, то есть в партнёрской программе.
- **Требования к URL.** Только HTTPS, порт 443 или 8443. Подходит любой сертификат, в том числе самоподписанный. TLS 1.2 или выше.
- **События для платежей:** `payment.waiting_for_capture`, `payment.succeeded`, `payment.canceled`, `refund.succeeded`, `payment_method.active`. Имя события строится как `<объект>.<статус>`.
- **Формат тела:** `{"type": "notification", "event": "payment.succeeded", "object": {…полный объект платежа на момент события, включая metadata…}}`.
- **Ответ и повторы.** Нужно ответить **HTTP 200**, тело и заголовки ответа ЮKassa игнорирует. При любом другом коде ЮKassa «will continue sending out the notification for the next **24 hours** following the moment when the event occurred». Интервалы между повторами [не документированы].
- **Проверка подлинности.** Документация предлагает два способа: (1) проверить **актуальный статус объекта**, то есть запросить `GET /v3/payments/{id}`; (2) проверить **IP-адрес отправителя**. Подписи (HMAC) у уведомлений нет. [факт: других способов в документации нет]
- **Повторы и дубликаты.** [вывод] Одно и то же уведомление может прийти несколько раз, поэтому обработчик должен быть идемпотентным: зачисляем заказ один раз по `payment.id` / `order_id`. Источник истины — ответ `GET /v3/payments/{id}`, а не тело уведомления.
- **Тестовый магазин.** Документация советует указать для него отдельный URL уведомлений и не выдавать товары по тестовым платежам ([testing](https://yookassa.ru/developers/payment-acceptance/testing-and-going-live/testing)).

**IP-адреса уведомлений ЮKassa, дословно из [документации](https://yookassa.ru/developers/using-api/webhooks) (список одинаковый в русской и английской версиях, 2026-10-21):**

```
185.71.76.0/27
185.71.77.0/27
77.75.153.0/25
77.75.156.11
77.75.156.35
77.75.154.128/25
2a02:5180::/32
```

Список в SDK `yookassa` 3.13.0 (`src/yookassa/domain/common/security_helper.py`) — IPv4 совпадают, IPv6 уже:
`2a02:5180:0:1509::/64`, `2a02:5180:0:2655::/64`, `2a02:5180:0:1533::/64`, `2a02:5180:0:2669::/64`. [факт]

[вывод] Если сервер стоит за обратным прокси, реальный IP отправителя нужно брать из доверенного заголовка. Даже при правильном IP зачислять заказ только после `GET /v3/payments/{id}`.

### 1.4. Особенности для самозанятых (НПД)

- **[факт]** Самозанятые могут подключить ЮKassa: зарегистрироваться в «Мой налог», завести идентифицированный кошелёк ЮMoney, подать заявку ([conditions](https://yookassa.ru/docs/support/payments/conditions)). Подключение по API доступно («Встройте ЮKassa на сайт с помощью платёжного модуля или по API», [страница для самозанятых](https://yookassa.ru/platezhi-dlya-samozanyatyh/)).
- **[факт] Объект `receipt` самозанятому не нужен.** В спецификации `receipt` передаётся, только если «you are a company or a sole proprietor» и используете «Чеки от ЮKassa» или стороннюю онлайн-кассу ([OpenAPI, ReceiptData](https://yookassa.ru/developers/api/yookassa-openapi-specification.yaml)).
- **[факт] Чеки в «Мой налог» ЮKassa не передаёт.** Самозанятый сам регистрирует доход и отдаёт чек покупателю. Цитата: «Вы можете делать это вручную. В ЮKassa эта опция недоступна» ([receipts/basics](https://yookassa.ru/developers/payment-acceptance/receipts/basics)). На странице для самозанятых в ответ на вопрос «Должен ли самозанятый отправлять чеки покупателю?» сказано: «Да, должен. Это можно делать, например, в сервисе Мой налог».
- **[факт] Срок для чека по закону.** По 422-ФЗ ст. 14 ч. 3 при оплате электронными средствами платежа чек формируется и передаётся покупателю **в момент расчёта**. Срок «не позднее 9-го числа следующего месяца» относится к другим безналичным расчётам ([КонсультантПлюс, ст. 14](https://www.consultant.ru/document/cons_doc_LAW_311977/ad291257df0088ddbc91dc935ca07c7d40bde627/)). Относится ли оплата через ЮKassa (СБП, карта) к «электронным средствам платежа» в смысле этой нормы — вопрос к бухгалтеру. [не документировано]
- **[вывод]** Чеки придётся делать вручную в «Мой налог» или через партнёров ФНС, у которых есть API «Мой налог». Это отдельная интеграция вне ЮKassa. Если заказов будет много, это станет узким местом.
- **[факт] Ограничения** ([страница для самозанятых](https://yookassa.ru/platezhi-dlya-samozanyatyh/)):
  - платить могут только физлица;
  - с карт можно принять до 500 000 ₽ в месяц, лимиты по другим способам отличаются;
  - доход не выше 2,4 млн ₽ в год;
  - перепродажа запрещена;
  - деньги приходят в течение двух рабочих дней;
  - комиссия за цифровые товары и услуги по картам, SberPay, T-Pay и ЮMoney — 3,5% плюс НДС от суммы комиссии; по СБП — «Рассчитаем индивидуально».
- **[факт] Автоплатежи.** Для самозанятых заявлены («Есть автоплатежи… Подойдёт тем, кто продаёт что-то по подписке»). Общее правило для всех магазинов: в настоящем магазине автоплатежи включает менеджер; `save_payment_method` «Доступно только после согласования с менеджером ЮKassa» ([recurring-payments](https://yookassa.ru/developers/payment-acceptance/scenario-extensions/recurring-payments/basics), [OpenAPI](https://yookassa.ru/developers/api/yookassa-openapi-specification.yaml)). Отдельных запретов автоплатежей для самозанятых не нашёл. [не документировано]
- **[факт] Возвраты.** Отдельных ограничений возвратов для самозанятых в документации нет. Общие правила — в 1.5. При возврате самозанятый **вправе** скорректировать ранее переданные сведения о расчётах (422-ФЗ ст. 8 ч. 4), а сумма возврата уменьшает доход периода (ст. 8 ч. 3) ([КонсультантПлюс, ст. 8](https://www.consultant.ru/document/cons_doc_LAW_311977/c2d36c561e1cb3865295658c7014e0a0d10371cc/)). [вывод] На практике это аннулирование или корректировка чека в «Мой налог», вручную.
- **[факт]** Готовая интеграция ЮKassa с Telegram Payments (`provider_token` через @BotFather) принимает только карты, кошелёк ЮMoney и SberPay. **СБП, автоплатежей и холдирования там нет** ([telegram](https://yookassa.ru/docs/support/payments/onboarding/integration/cms-module/telegram)). Для цифровых товаров её запрещает и сам Telegram (см. 2.4). Поэтому для TS Proxy нужен прямой API и redirect. [вывод]

### 1.5. Возвраты и автоплатежи

**Возвраты** [факт] ([refunds](https://yookassa.ru/developers/payment-acceptance/after-the-payment/refunds), [поддержка: refunding](https://yookassa.ru/docs/support/payments/refunding)):
- Запрос: `POST /v3/refunds` с `payment_id`, `amount` и заголовком `Idempotence-Key`. Возврат бывает полным или частичным.
- Вернуть можно только платёж в статусе `succeeded` и только на то же средство оплаты.
- За возврат комиссия не берётся, но комиссия за сам платёж не возвращается.
- Срок, в течение которого можно сделать возврат, — 3 года для большинства способов. Для карт эквайер может отказать, если платёж старше 15 месяцев.
- Возвраты списываются с ещё не выплаченных вам сумм. Если их не хватает — с баланса обеспечения возвратов ([refunding](https://yookassa.ru/docs/support/payments/refunding)).
- Возврат также можно сделать из личного кабинета.
- О возврате приходит событие `refund.succeeded`.

**Автоплатежи** [факт] ([recurring-payments](https://yookassa.ru/developers/payment-acceptance/scenario-extensions/recurring-payments/basics), [sbp](https://yookassa.ru/developers/payment-acceptance/integration-scenarios/manual-integration/other/sbp)):
- В тестовом магазине автоплатежи есть по умолчанию, в настоящем их включает менеджер.
- Способ оплаты можно сохранить двумя путями: во время платежа (`save_payment_method: true`) или привязкой без списания. Списание по сохранённому способу — через `payment_method_id`.
- Удалить сохранённый способ на стороне ЮKassa нельзя. Чтобы отключить автоплатёж, перестаём использовать сохранённый способ у себя.
- Если пользователь отозвал разрешение через банк, платёж отменится с причиной `permission_revoked`.
- По СБП автоплатежи поддерживают не все банки. При `save_payment_method: true` в списке остаются только банки, которые их поддерживают.
- Перед подключением нужна оферта с условиями автоплатежей и способ их отключить.

### 1.6. Тестовый магазин

[факт] ([testing](https://yookassa.ru/developers/payment-acceptance/testing-and-going-live/testing)):
- Тестовый магазин создаётся сразу после регистрации, даже до договора. Можно создать до 20 тестовых магазинов. У тестового магазина свои `shopId` и ключ, а в объекте платежа `test: true`.
- В тестовом магазине доступны все основные возможности API, включая автоплатежи.
- **Протестировать можно только карты и кошелёк ЮMoney.** «Other payment methods can only be tested in a real store» — **СБП в тестовом режиме нет.**
- Тестовые карты: срок действия — любой будущий, CVC и код 3-D Secure — любые.

| Номер | Тип | 3-D Secure |
|---|---|---|
| 5555555555554477 | Mastercard | да |
| 5555555555554444 | Mastercard | нет |
| 4111111111111111 | Visa | нет |
| 4793128161644804 | Visa | да |
| 2200000000000004 | Mir | да |
| 2202474301322987 | Mir | нет |

- Для автоплатежей нужны карты без 3-D Secure.
- Отказы можно проверить специальными картами, например:
  - `5555555555554600` — `insufficient_funds`;
  - `5555555555554592` — `3d_secure_failed`;
  - полный список — на странице testing.

### 1.7. Python SDK `yookassa` или свой клиент на `httpx`

[факт] (PyPI JSON API; распакованный sdist `yookassa-3.13.0.tar.gz`):
- **Актуальность.** Последняя версия — 3.13.0 от 2026-10-06. Предыдущие: 3.12.x (август 2026), 3.11.0 (июнь 2026). SDK выпускается регулярно.
- **Репозиторий на GitHub `yoomoney/yookassa-sdk-python` не обновляется:** последний коммит в master — 2022-03-22 (v2.3.0). На PyPI домашней страницей указан `git.yoomoney.ru/projects/SDK/repos/yookassa-sdk-python`, то есть разработка переехала туда.
- **Только синхронный.** Зависимости: `requests`, `urllib3`, `netaddr`, `distro`, `deprecated`. Асинхронного API нет: в коде нет `async def`, `aiohttp` и `httpx`.
- **Нет сетевого таймаута.** `session.request(...)` в `client.py` вызывается без `timeout=`, то есть зависший запрос будет ждать бесконечно. Параметр `Configuration.timeout = 1800` используется только как `backoff_factor` (`timeout/1000`) для повторов.
- **Повторы.** `Retry(total=max_attempts(3), allowed_methods=['POST'], status_forcelist=[202])` — повторяются только POST-запросы с ответом 202.
- **Ключ идемпотентности.** Если ключ не передан, SDK генерирует `uuid.uuid4()` при каждом вызове `Payment.create`, поэтому при повторе на нашей стороне ключ будет другим.
- **Уведомления.** Есть `WebhookNotificationFactory` и `SecurityHelper.is_ip_trusted`, но список IPv6 там уже, чем в документации (см. 1.3).
- **Публичные функции на уровне класса.** Конфигурация глобальная: `Configuration.configure(...)`.

[вывод] Для aiogram 3 и FastAPI с asyncio лучше **свой тонкий клиент на `httpx.AsyncClient`**. Нужно всего 3–4 вызова: `POST /payments`, `GET /payments/{id}`, `POST /refunds`, позже, возможно, автоплатёж. Плюсы своего клиента:
- явный таймаут;
- ключ идемпотентности из нашего `order_id` и номера попытки;
- повтор при 500 или таймауте с тем же ключом;
- проверка IP по списку из документации;
- модели можно описать по [OpenAPI-спецификации](https://yookassa.ru/developers/api/yookassa-openapi-specification.yaml).

Если всё же брать SDK, вызывать его через `asyncio.to_thread` и всегда передавать свой `idempotency_key`.

---

## Часть 2. Telegram Stars

### 2.1. Продажа цифровой услуги за Stars

[факт] ([payments-stars](https://core.telegram.org/bots/payments-stars), [Bot API](https://core.telegram.org/bots/api)):
- **`sendInvoice`:** `currency="XTR"`, `provider_token` — пустая строка или не передаётся. В `prices` — **ровно один элемент**. `payload` — 1–128 байт, пользователь его не видит; сюда кладём `order_id`. `title` — 1–32 символа, `description` — 1–255.
- **`pre_checkout_query`:** нужно ответить `answerPreCheckoutQuery` **в течение 10 секунд**, иначе транзакция отменяется. При `ok=false` передаётся `error_message`, его покажут пользователю.
- **`successful_payment`:** товар выдаётся только после этого сообщения. «Simply answering a pre_checkout_query does not guarantee a successful order or payment». Нужно сохранить `telegram_payment_charge_id` — по нему делается возврат.
- **Пересылка счёта.** Счёт, отправленный в чат с ботом, оплачивается один раз (single-chat). Пересланный счёт или счёт в другом чате может быть оплачен много раз, поэтому бот сам решает, принимать ли повторные оплаты. [вывод] Проверять в `pre_checkout_query`, что заказ ещё не оплачен.
- **Подписки:** `createInvoiceLink` с `subscription_period`, только для XTR. Сейчас это значение **всегда 2592000 (30 дней)**. Цена подписки — не больше **10 000 Stars**. У одного пользователя может быть несколько подписок одновременно.
  - У продлений в `SuccessfulPayment` есть `is_recurring`, `is_first_recurring`, `subscription_expiration_date`.
  - **`editUserStarSubscription(user_id, telegram_payment_charge_id, is_canceled)`** — бот отменяет продление (подписка работает до конца периода) или снова его разрешает.
  - С Bot API 10.2 (2026-07-14) есть апдейт **`subscription` / `BotSubscriptionUpdated`** с состоянием `canceled`, `active` или `failed` ([changelog](https://core.telegram.org/bots/api-changelog)).
- **Возврат:** `refundStarPayment(user_id, telegram_payment_charge_id)` возвращает пользователю всю сумму. После этого приходит сервисное сообщение `refunded_payment` (`RefundedPayment`). Возврат по ToS идёт «with no penalty».
- **Обязательные команды:** `/terms` (рекомендована), `/support`, **`/paysupport`** — обязательна. Споры решает продавец; если не решать, Telegram может списать Stars, поставить метку SCAM или удалить бота ([ToS §6.2.1](https://telegram.org/tos/bot-developers)).
- **Чарджбэки.** Если покупатель оспорит покупку Stars в Apple или Google, Telegram может удержать или списать Stars с баланса бота. Поэтому Telegram «expressly discourages the real-time sale of digital products… which… cannot be reclaimed». [вывод] Для TS Proxy доступ нужно уметь отзывать после возврата или чарджбэка.
- **Тестирование:** в тестовом окружении Telegram Stars бесплатны.
- **aiogram:** последняя версия на PyPI — 3.31.0 от 2026-08-26. Поддерживает ли она Bot API 10.2 (`BotSubscriptionUpdated`) — не проверял. [не документировано]

### 2.2. Курс и сколько получает продавец

[факт] Таблица Telegram ([payments-stars → Star Pricing](https://core.telegram.org/bots/payments-stars), картинка). Цены для покупки через App Store или Google Play, в USD, без НДС:

| Stars | Платит пользователь | Apple/Google 30% | Административные расходы Telegram | Получает разработчик |
|---|---|---|---|---|
| 50 | $0.99 | $0.30 | $0.04 | $0.65 |
| 100 | $1.99 | $0.60 | $0.09 | $1.30 |
| 250 | $4.99 | $1.50 | $0.24 | $3.25 |
| 500 | $9.99 | $3.00 | $0.49 | $6.50 |
| 1000 | $19.99 | $6.00 | $0.99 | $13.00 |
| 2500 | $49.99 | $15.00 | $2.49 | $32.50 |

- **Продавец** получает фиксированно **0,013 USD за 1 Star** в виде вознаграждения ([ToS §6.2.4](https://telegram.org/tos/bot-developers)). Если тратить Stars на рекламу Telegram Ads — 0,02 USD за Star (§6.2.3). Telegram оговаривает, что это значение не связано с ценой покупки Stars и может меняться. [факт]
- **Покупатель** в магазине приложений платит около $0,02 за Star. Значит, продавец получает **около 65% от цены для пользователя**. [вывод из таблицы]
- **Цена в рублях** в первоисточниках **не указана**. Она зависит от магазина приложений, страны, налогов и способа покупки: In-App Purchase, @PremiumBot, Fragment ([tos/stars §2, §2.1](https://telegram.org/tos/stars)). Актуальные курсы клиент получает из конфигурации (`stars_usd_sell_rate_x1000`, `stars_usd_withdraw_rate_x1000`, [api/config](https://core.telegram.org/api/config)), но числа в документации не опубликованы. [факт / не документировано]
- **Комиссия 15%.** Если включить темы (topics) в личных чатах бота, Telegram удерживает невозвратную комиссию 15% со всех покупок за Stars (§6.2.6). [факт] Для TS Proxy темы не включать. [вывод]

### 2.3. Вывод денег

[факт]:
- **Как выводить.** Только через Fragment ([ToS §6.2.4.1](https://telegram.org/tos/bot-developers), [api/stars → Withdrawing revenue](https://core.telegram.org/api/stars)). Владелец бота запрашивает вывод, нужен **пароль двухфакторной аутентификации**. Telegram выдаёт уникальную ссылку на Fragment, там указывается адрес **TON-кошелька**.
- **Чем платят.** Fragment по умолчанию платит **Gram ($GRAM)** по рыночному курсу coinmarketcap, курс обновляется примерно каждые 5 минут. «In some countries» возможны другие способы, например стейблкоины, на усмотрение Fragment ([Fragment Terms §5–6.1](https://fragment.com/terms)). Транзакции необратимы.
- **Минимум:** «You cannot withdraw less than 1000 stars» ([строка перевода Telegram](https://translations.telegram.org/en/macos/unsorted/Monetization.Stars.WithdrawInfo)). Формально порог задаётся параметром конфигурации `stars_revenue_withdrawal_min`, есть и максимум `stars_revenue_withdrawal_max` ([api/stars](https://core.telegram.org/api/stars)).
- **Задержка:** Stars становятся доступны для вывода или рекламы через срок **до 21 дня** после получения (§6.2.4).
- **Срок действия:** Stars действуют **3 года**, потом сгорают (§6.2.7).
- **Проверка личности (KYC).** В условиях Fragment и Telegram о ней **ничего не сказано**. Fragment называет себя «non-custodial» и говорит, что покупать можно «anonymously». [не документировано для вывода Stars]
- **Граждане и резиденты РФ.** Прямо о России в первоисточниках ничего нет. Есть общие оговорки:
  - «Fragment may be unable to issue rewards for certain users or in certain countries»;
  - Fragment «may limit which countries it operates in»;
  - Telegram не компенсирует невозможность вывода (§6.2.4.1, Fragment Terms §3).
  
  [не документировано] Работает ли вывод для граждан РФ сейчас, проверяется только на практике.
- **Налоги:**
  - по условиям все налоги платит сам продавец, Telegram ничего не считает и не удерживает (§6.4);
  - Fragment тоже перекладывает налоги и соблюдение местных законов на пользователя (Terms §3). [факт]
  
  [вывод, нужен бухгалтер] Доход приходит от зарубежной компании и в криптовалюте. Можно ли учитывать его в НПД — неясно: в НПД ставка 6% с юрлиц, а 422-ФЗ ограничивает расчёты. Как оформить чек и как пересчитывать в рубли — не ясно.

### 2.4. Правило «цифровые товары в ботах — только за Stars» в 2026 году

[факт] Правило действует. Bot Developer ToS §6.2:
> «Due to requirements from stores that host Telegram apps, transactions for digital goods and services cannot be processed through third-party payment providers. Accordingly, all transactions pertaining to digital goods and services must be executed exclusively through the exchange of Telegram Stars».

FAQ на [payments-stars](https://core.telegram.org/bots/payments-stars):
> «your bot or mini app must use Telegram Stars for the sale of digital goods and services inside Telegram apps, regardless of any other web portals, apps, services or payment providers you may have set up outside the Telegram ecosystem».

Что будет за нарушение (§6.2):
> «If your TPA is found to be using alternative payment systems for digital goods and services, you may receive a notice… Should you fail to comply with such notices, we may have to make your TPA inaccessible from store versions of Telegram or terminate it from Bot Platform».

Что считается цифровым товаром, Telegram определяет по правилам Apple и Google (§6.2).

[вывод]
- TS Proxy — цифровая услуга. Оплата через ЮKassa по ссылке из бота формально нарушает §6.2.
- Санкция ступенчатая: сначала предупреждение владельцу бота, потом бот могут скрыть в версиях Telegram из App Store и Google Play или удалить.
- Как часто это применяют на практике, Telegram не публикует. [не документировано]
- Меньше всего риск, если Stars будут полноценным способом оплаты внутри бота, а внешняя оплата пойдёт через сайт TS Proxy, а не через кнопку в боте. Но FAQ прямо говорит «regardless of any other web portals», так что риск остаётся и в этом случае.

---

## Что спросить у поддержки ЮKassa

1. Есть ли для самозанятого в настоящем магазине доступ к API (`POST /v3/payments`) с СБП и картами по redirect? Какая комиссия по СБП?
2. Подключат ли самозанятому автоплатежи (`save_payment_method`) и на каких условиях?
3. От какого момента считается час жизни `pending`-платежа, если `payment_method_data` не передан?
4. Подтвердить, что уведомления приходят только с IP из документации, включая весь `2a02:5180::/32`.
5. Есть ли ограничения на возвраты через API для самозанятого? Откуда списывается возврат, если невыплаченных сумм не хватает?
