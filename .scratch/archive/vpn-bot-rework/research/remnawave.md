# Исследование: возможности Remnawave и Python SDK для наших тарифов

Тикет: `.scratch/vpn-bot-rework/issues/01-vozmozhnosti-remnawave.md`
Дата: 2026-10-20. Источники: исходники backend `remnawave/backend` на теге **3.4.5** (последний релиз, 2026-10-06), исходники SDK из PyPI (`remnawave` 2.8.0 и `remnawave-api` 3.4.5), docs.rw. Запросов к нашей панели не делалось, поэтому **версия нашей панели неизвестна**. Это первое, что нужно проверить (тикет 03).

Сокращения в ссылках:
- `BE:` — https://github.com/remnawave/backend/blob/3.4.5/…
- `SDK2:` — PyPI `remnawave==2.8.0` (repo https://github.com/remnawave/python-sdk, ветка `production`)
- `SDK3:` — PyPI `remnawave-api==3.4.5` (repo https://github.com/sm1ky/remnawave-api)

---

## 0. Главное (влияет на всё остальное)

1. **В панели 3.0.0 (2026-07-31) API сломан несовместимо.** Поле `uuid` у пользователя удалено, пользователь теперь определяется числовым `id`. Пути `/users/{uuid}` заменены на `/users/{userId}` (число). Эндпоинт `GET /users/by-telegram-id/{telegramId}` удалён, вместо него фильтр `telegramId` у `GET /users/stream`. HWID-эндпоинты тоже принимают `userId` (число).
   - Было (2.8.1): `UsersSchema` начинается с `uuid: z.string().uuid()`, есть `TELEGRAM_ID: by-telegram-id/...` — `https://raw.githubusercontent.com/remnawave/backend/2.8.1/libs/contract/models/users.schema.ts`, `.../2.8.1/libs/contract/api/controllers/users.ts`.
   - Стало (3.0.0+): `BE: libs/contract/models/users.schema.ts` (нет `uuid`, первое поле `id`), `BE: libs/contract/api/controllers/users.ts` (`DELETE: (userId)`, `GET_BY_ID: (userId)`, в `GET_BY` только `SHORT_UUID` и `USERNAME`), `BE: libs/contract/commands/users/delete-user.command.ts` (`userId: numberParamSchema`, то есть `z.coerce.number().positive()`).
2. **Пакет `remnawave` (PyPI, 2.8.0), который импортирует bot.py, заархивирован.** Репозиторий `remnawave/python-sdk` имеет `archived: true` (GitHub API), а docs.rw/sdk/python-sdk пишет: «This SDK is no longer maintained». Последняя версия 2.8.0 (2026-07-02) объявлена совместимой с панелью `>=2.8.0` (README, таблица «Compatible versions»). Это написано до выхода 3.0. Модель `UserResponseDto` в SDK2 требует поле `uuid: UUID` (`SDK2: remnawave/models/users.py`), поэтому **на панели 3.x любой ответ с пользователем упадёт с pydantic ValidationError**, а `get_users_by_telegram_id` получит 404 (маршрута нет).
   - Кроме того, даже на панели 2.8.x HWID-модели SDK2 сломаны: `HwidDeviceDto.user_uuid` обязательно, а панель присылает `userId`. См. issues #58, #62, #63 в remnawave/python-sdk (последний — падение `WebhookUtility.parse_webhook` на HWID-вебхуках). Исправлений не будет, потому что репозиторий в архиве.
3. **Работу продолжает тот же автор (Artem / sm1ky) под старым именем пакета `remnawave-api`.** Версия 3.4.5 от 2026-09-25, summary «A Python SDK for interacting with the Remnawave API v3.4.4». Таблица README `SDK3`: `3.4.5 → панель >=3.4.4`, `3.4.3 → >=3.4.0`, `3.2.x → >=3.2.0`, `3.0.1 → >=3.0.0`, `2.8.1 → >=2.8.0`. Python-модуль называется так же, `remnawave` (`pyproject.toml`: `packages = [{ include = "remnawave" }]`), поэтому два пакета **нельзя ставить одновременно**. Официальной страницы docs.rw этот пакет не имеет, на docs.rw/awesome-remnawave Python-SDK тоже не перечислены. Это community-пакет, но от прежнего мейнтейнера.
   - Вывод: версию SDK выбирать строго под версию панели. Для панели 3.4.x это `remnawave-api==3.4.5` (или пин по таблице). Для панели 2.8.x подходит `remnawave==2.8.0`, но без HWID-моделей, либо `remnawave-api==2.8.1`.

Конкретные места bot.py, которые сломаются на панели 3.x (только чтение, ничего не правилось):
- `get_attr(existing_user, "uuid")`, `UpdateUserRequestDto(uuid=...)`, `delete_user(str(uuid))`: в 3.x используется `id: int`, а в SDK3 методы называются `update_user(UpdateUserBodyDto(id=...))` и `delete_user(user_id: int)`;
- `get_users_by_telegram_id(...)`: в SDK3 метода нет, вместо него `users.get_users_stream(telegram_id="...")`;
- `from remnawave.models import CreateUserRequestDto, UpdateUserRequestDto`: в SDK3 классы называются `CreateUserBodyDto` и `UpdateUserBodyDto` (`SDK3: remnawave/models/users.py`);
- `except Exception: pass` в `get_profile_remnawave_user` не отличает «не найден» от «сеть упала» (см. §5).

---

## 1. Лимит устройств (HWID)

**Как включается.** Только на уровне панели: *Subscription → Settings → HWID Device Limit*. Можно переопределять для каждого External Squad. По умолчанию выключено (docs.rw/features/hwid-device-limit). Через API это поле `hwidSettings` в настройках подписки: `{ enabled: bool, fallbackDeviceLimit: int, maxDevicesAnnounce?: str ≤200 }` (`SDK2/SDK3: models/subscriptions_settings.py`, `HwidSettingsDto`; `BE: libs/contract/models/subscription-settings/hwid-settings.schema.ts`). Метод SDK: `subscriptions_settings.update_settings(...)`.

**Лимит для конкретного пользователя.** Да, через API/SDK. Поле `hwidDeviceLimit` (`hwid_device_limit` в SDK) есть в create и update:
- create: `hwidDeviceLimit: z.int().min(0)`, необязательное (`BE: libs/contract/commands/users/create-user.command.ts`);
- update: `hwidDeviceLimit: z.int().min(0).nullish()`, можно сбросить в `null` (`BE: .../update-user.command.ts`).

Семантика (`BE: src/modules/subscription/subscription.service.ts`, `checkHwidDeviceLimit`, строки ~738–870):
- `hwidDeviceLimit === 0` — лимит для пользователя **отключён** (`limitBypassed: true`), устройства только записываются;
- `null` — действует `fallbackDeviceLimit` из настроек (`user.hwidDeviceLimit ?? hwidSettings.fallbackDeviceLimit`);
- `N > 0` — допускается N устройств. Новое устройство добавляется под advisory lock, а при превышении возвращается `LIMIT_REACHED`.

Для тарифов «1/3/5 устройств» достаточно передавать `hwid_device_limit=N` при создании и продлении. Глобальная настройка `enabled` должна быть включена.

**Что видит клиент при превышении** (тот же файл, строки ~173–245). Подписка отдаётся **пустой** (`body: ''`, `text/plain`) с заголовками `x-hwid-active: true`, `x-hwid-max-devices-reached: true`, `x-hwid-limit: true` (для v2RayTun). Если в настройках задан `maxDevicesAnnounce`, добавляется заголовок `announce` с этим текстом: Happ показывает его как объявление, поддерживаются шаблонные переменные. Если включён `isShowCustomRemarks`, вместо пустого ответа клиент получает «заглушку» с текстами из `customRemarks.HWIDMaxDevicesExceeded`, то есть «серверы»-надписи. Если клиент не прислал `x-hwid`, срабатывает `HWIDNotSupported` и `x-hwid-not-supported: true`. Docs: «Remnawave will return a 404 error if no HWID header is sent». В коде это пустой ответ или заглушка.
- Клиенты, которые присылают `x-hwid`: Happ, v2RayTun, Koala Clash, FlClashX, Prizrak-Box, Incy, Passwall-OpenWRT, RenoarX, DeskBox. У Throne, Shadowrocket, Clash Mi и Karing это выключено по умолчанию (docs.rw/features/hwid-device-limit). С панели 3.0.0 `x-hwid` проверяется по `/^[a-zA-Z0-9=-]{10,64}$/`, невалидное значение игнорируется (там же; релиз 3.0.0 «Enforce HWID format validation»).
- Вывод из кода (не из документации): лимит проверяется **только при скачивании подписки**. Прокси-ключ (vless uuid) у всех устройств пользователя общий. Если ключ вытащить вручную или клиент не обновляет подписку, лимит не помешает.

**Список и удаление устройств через API** (`BE: libs/contract/api/controllers/hwid.ts`; `SDK3: controllers/hwid.py`, атрибут `sdk.hwid`):

| Действие | HTTP | SDK3 |
|---|---|---|
| устройства пользователя | `GET /api/hwid/devices/{userId}` | `hwid.get_hwid_user(user_id: int)` → `{total, devices[]}` |
| удалить одно | `POST /api/hwid/devices/delete` `{userId, hwid}` | `hwid.delete_hwid_to_user(HWIDDeleteRequest(user_id=, hwid=))` |
| удалить все | `POST /api/hwid/devices/delete-all` `{userId}` | `hwid.delete_all_hwid_user(DeleteUserAllHwidDeviceRequestDto(...))` |
| добавить вручную | `POST /api/hwid/devices` | `hwid.add_hwid_to_users(...)` |
| все / статистика / топ | `GET /api/hwid/devices`, `/stats`, `/top-users` | `get_hwid_users`, `get_hwid_stats`, `get_top_users_by_hwid_devices` |

Поля устройства: `hwid, userId, platform, osVersion, deviceModel, userAgent, requestIp, createdAt, updatedAt` (`BE: libs/contract/models/hwid-user-device.schema.ts`). Этого хватит для кнопки «мои устройства → удалить».

## 2. Ссылка и страница подписки

**Что отдаёт сама ссылка подписки клиенту.** Заголовок `subscription-userinfo` (upload/download/total/expire — стандарт, который клиенты показывают как трафик и срок), `subscription-refill-date`, а также `profile-title`, `support-url`, `announce` и `profile-update-interval` из настроек. Произвольные `customResponseHeaders` поддерживают шаблонные переменные (`BE: src/modules/subscription/subscription.service.ts`, `getUserProfileHeadersInfo`). Переменные: `DAYS_LEFT, TRAFFIC_USED, TRAFFIC_LEFT, TOTAL_TRAFFIC, STATUS, USERNAME, TELEGRAM_ID, SUBSCRIPTION_URL, EXPIRE_UNIX, SS_HWID_LIMIT, …` (`BE: src/common/utils/templates/replace-templates-values.ts`). Для пользователей в статусах EXPIRED, LIMITED и DISABLED и для HWID-случаев можно показывать «заглушки»-remarks (`customRemarks`: `expiredUsers, limitedUsers, disabledUsers, emptyHosts, HWIDMaxDevicesExceeded, HWIDNotSupported`, `SDK*/models/subscriptions_settings.py`).

**Страница подписки** — отдельный контейнер `remnawave/subscription-page`, ставится на тот же или на отдельный сервер (docs.rw/install/remnawave-subscription-page). Данные берёт из `GET /api/sub/{shortUuid}/info`: `username, daysLeft, expiresAt, trafficUsed, trafficLimit, lifetimeTrafficUsed, isActive, userStatus, trafficLimitStrategy`, ссылки (`BE: libs/contract/models/subscription-info.schema.ts`). **Количества устройств и HWID-лимита в этой схеме нет**, на странице их не показать. Это видно в боте через HWID API. При включённом HWID ключи (`links`) на странице не отдаются (`if (!settings.hwidSettings.enabled || authenticated)` в `subscription.service.ts` ~513).

**Настраиваемость.** Да. Конфиги страницы хранятся в панели (`/api/subscription-page-configs`, CRUD, clone и reorder: `SDK*: controllers/subscription_page.py`), а схема описана в `BE: libs/subscription-page/models/subscription-page-config.schema.ts`:
- `platforms` — `ios, android, linux, macos, windows, androidTV, appleTV` (`constants/platform-types.constant.ts`). На каждую платформу задаётся список `apps` (name, featured, blocks с кнопками `external | subscriptionLink | copyButton`);
- `branding`: `title, logoUrl, supportUrl`;
- `uiConfig`: вид блока информации (`collapsed|expanded|cards|hidden`) и вид инструкций (`cards|accordion|minimal|timeline`);
- `locales` и переводы, `metaTitle/metaDescription`, `showConnectionKeys`, `hideGetLinkButton`;
- шаблонные ключи в ссылках кнопок: `USERNAME, SUBSCRIPTION_LINK, HAPP_CRYPT3_LINK, HAPP_CRYPT4_LINK, INCY_CRYPT1_LINK` (`constants/template-keys.constant.ts`);
- External Squad может переопределять настройки и шаблоны подписки (release 3.0.0 «external squad template caching»; `applyMaybeExternalSquadOverrides`).

Ссылку подписки для бота даёт поле `subscriptionUrl` в ответе пользователя. В SDK есть хелпер `user.happ` / `happ_with_version("v4")` для зашифрованной deeplink-ссылки Happ (`SDK*: models/users.py`).

## 3. Ограничение скорости на пользователя

**Нет.** В схеме пользователя нет полей скорости (`BE: libs/contract/models/users.schema.ts`), а поиск по backend (`speed`, `bandwidthLimit`, `throttl`) ничего не находит, кроме параметров `up_mbps/down_mbps` для Hysteria/Brutal в генераторе sing-box. Это подсказка клиенту, а не серверный лимит. Плагины нод — только Torrent Blocker, блок IP и Connection Drop (`BE: libs/node-plugins/models/node-plugins.schema.ts`). «До 1 Гбит/с» в оффере — это свойство канала ноды, а не настройка Remnawave. Ограничить скорость можно только средствами ОС на ноде (tc), и то лишь на всю ноду, не на пользователя. Ограничения по объёму трафика есть: `trafficLimitBytes` + `trafficLimitStrategy` (`NO_RESET|DAY|WEEK|MONTH|MONTH_ROLLING`).

## 4. Как моделировать пробные 24 часа

Отдельного понятия «trial» в Remnawave нет (grep `trial` по backend пуст). Предлагаю такую модель на штатных полях:
- `expire_at = now + 24h`. Панель каждые 30 секунд переводит ACTIVE и LIMITED с истёкшим сроком в `EXPIRED` и шлёт событие `user.expired` (`BE: src/scheduler/intervals.ts` `FIND_EXPIRED_USERS: EVERY_30_SECONDS`; `src/modules/users/repositories/users.repository.ts` ~179–190; `src/queue/_users/processors/users-watchdog.processor.ts`).
- `traffic_limit_bytes = <N ГБ>`, `traffic_limit_strategy = NO_RESET` → при исчерпании `LIMITED` + `user.limited` (каждые 45 секунд).
- `hwid_device_limit = 1`.
- `tag = "TRIAL"` (A–Z, 0–9, `_`, ≤16 символов) — для фильтра `get_users_stream(tag="TRIAL")` и в админке; `description` — свободный текст.
- `telegram_id` — чтобы находить пробный аккаунт через `get_users_stream(telegram_id=...)`.
- Отдельный Internal Squad для пробных — опционально: если пробникам нужны другие ноды или инбаунды (`active_internal_squads=[TRIAL_SQUAD]`). Отдельный External Squad — если нужны другие тексты и настройки подписки (announce, remarks, HWID override).
- Переход «пробный → платный»: можно продлевать того же пользователя (`update_user`: новый `expire_at`, `trafficLimitBytes=0`, другой лимит устройств, другой squad), а можно создавать отдельного. Признак «пробный уже выдан» надо хранить у себя в БД: если пробного пользователя удалить, в панели следа не останется.
- Подводные камни update в 3.x (`BE: .../update-user.command.ts`): `status` принимает только `ACTIVE|DISABLED`, а `expireAt` в прошлом отклоняется («Expiration date cannot be in the past»). Если у EXPIRED-пользователя поменять `expireAt` на будущее и не передать `status`, он станет ACTIVE автоматически. LIMITED становится ACTIVE, если увеличить `trafficLimitBytes` или выставить 0 (`BE: src/modules/users/users.service.ts` ~173–205).
- В 3.0.0 появился `POST /users/{userId}/actions/extend {days}`. EXPIRED продлевается от текущего момента и становится ACTIVE, ACTIVE — от текущего `expireAt`. DISABLED и LIMITED продлеваются, но статус не меняют (`BE: .../actions/extend-user.command.ts`; `SDK3: users.extend_user(user_id, ExtendUserBodyDto(days=))`). Это заменяет ручной расчёт `base_date` в bot.py. Шаг только в днях.

## 5. Поля, форматы и ошибки SDK

**Регистр.** API работает в camelCase (`expireAt`, `trafficLimitBytes`, `userTraffic.usedTrafficBytes`, `subscriptionUrl`). В SDK это pydantic-модели с **snake_case-атрибутами** и alias на camelCase (`expire_at: datetime = Field(alias="expireAt")`). Запросы сериализуются через `serialization_alias`. Даты приходят уже как `datetime` (ISO 8601 с `Z`, aware UTC), а не строкой. Отсюда:
- `user.expire_at` → `datetime`; `user.used_traffic_bytes` — свойство-обёртка над `user.user_traffic.used_traffic_bytes`; `user.traffic_limit_bytes` → `float` (0 = безлимит); `user.subscription_url`; `user.hwid_device_limit` → `int | None`; `user.telegram_id` → `int | None`; `user.active_internal_squads` → `[{uuid, name}]`.
- SDK2 (2.8): есть `uuid: UUID` и `id: int`. SDK3 (3.x): только `id: int` (docstring «uuid removed»).
- `telegram_id` в create и update — **int** (`Optional[int]`). bot.py передаёт `str(user_id)`, pydantic в lax-режиме это приводит, но правильно передавать int.

**Статусы:** `UserStatus(StrEnum)`: `ACTIVE, DISABLED, LIMITED, EXPIRED` (`SDK*: remnawave/enums/users.py`; `BE: libs/contract/constants/users`). Это `StrEnum`, поэтому `user.status == "ACTIVE"` работает, а `normalize_status` в bot.py не нужен. Значение `LIMITED` значит «трафик исчерпан, доступа нет». bot.py сейчас считает LIMITED активной подпиской, и это стоит пересмотреть. `TrafficLimitStrategy`: `NO_RESET, DAY, WEEK, MONTH, MONTH_ROLLING`.

**Ответы-списки.** В SDK2 `get_users_by_telegram_id` возвращает `TelegramUserResponseDto` (RootModel со списком: итерируется, есть `len`). В SDK3 `get_users_stream(...)` возвращает `{users: [...], next_cursor, has_more}`. `get_user_by_username` возвращает одного пользователя.

**Исключения** (`SDK*: remnawave/exceptions/general.py`, `handler.py`, `rapid/client.py`; в SDK2 и SDK3 код одинаковый):
- базовый класс `ApiError(status_code, error)` со свойствами `.code` (код вида `A025`), `.message`, `.status_code`;
- по HTTP-коду или коду ошибки: `BadRequestError` (400 и 429), `UnauthorizedError` (401), `ForbiddenError` (403), `NotFoundError` (404), `ConflictError` (409, например `A019 USER_USERNAME_ALREADY_EXISTS`), `ValidationError` (422), `ServerError` (5xx);
- **«не найден»**: `get_user_by_username` на несуществующего возвращает 404 с `errorCode: "A025"` (`USER_NOT_FOUND`, `BE: libs/contract/constants/errors/errors.ts`), SDK бросает `NotFoundError`;
- **сеть, таймаут, DNS, SSL**: `httpx.RequestError` перехватывается и превращается в **базовый `ApiError`** с `status_code == 0` и `code == "NETWORK_ERROR"`. Это *не* подкласс `NetworkError`, хотя такой класс объявлен. Ловить так: `except NotFoundError` → нет пользователя; `except ApiError as e: if e.status_code == 0` → сеть;
- **неверный токен**: 401 → `UnauthorizedError`;
- **несовпадение схемы** (например, SDK2 на панели 3.x) — `pydantic.ValidationError`, а не `ApiError`;
- импорт: `from remnawave.exceptions import ApiError, NotFoundError, ...`.

**Методы** (атрибут `sdk.users`):

| Задача | SDK2 `remnawave==2.8.0` (панель 2.8) | SDK3 `remnawave-api==3.4.5` (панель ≥3.4.4) |
|---|---|---|
| по username | `get_user_by_username(username)` | то же |
| по telegram | `get_users_by_telegram_id(str)` | `get_users_stream(telegram_id="…")` |
| по id | `get_user_by_uuid(uuid)` / `get_user_by_id(id)` | `get_user_by_id(user_id: int)` |
| создать | `create_user(CreateUserRequestDto)` | `create_user(CreateUserBodyDto)` |
| изменить | `update_user(UpdateUserRequestDto(uuid=…))` | `update_user(UpdateUserBodyDto(id=… или username=…))` |
| продлить | — | `extend_user(user_id, ExtendUserBodyDto(days=))` |
| удалить | `delete_user(uuid: str)` → `{isDeleted}` | `delete_user(user_id: int)` → `None` (204) |
| вкл/выкл, сброс трафика, перевыпуск ссылки | `enable_user/disable_user/reset_user_traffic/revoke_user_subscription(uuid)` | те же по `user_id: int` |
| HWID | `sdk.hwid.*` по `user_uuid` (модели ответа сломаны, issues #58/#62) | `sdk.hwid.*` по `user_id: int` |

Конструктор `RemnawaveSDK(base_url, token, caddy_token=None, ssl_ignore=…, client=httpx.AsyncClient|None, custom_headers=…)` (`SDK*: remnawave/__init__.py`). Требуется Python ≥3.11 (`requires_python` в SDK2: `>=3.11,<3.15`).

## 6. Вебхуки

Включаются в `.env` панели (не через API): `WEBHOOK_ENABLED=true`, `WEBHOOK_URL=` (несколько через запятую, с 3.0.0), `WEBHOOK_SECRET_HEADER=` (ровно 64 символа `a-zA-Z0-9`) (`BE: .env.sample`; docs.rw/features/webhooks).
- Тело: `{scope, event, timestamp(ISO), data, meta}`. Для `user.*` в `data` лежит полный пользователь, как в API (`BE: src/integration-modules/notifications/webhook-module/events/webhook.events.ts`). Для `user_hwid_devices.*`: `data = {user, hwidUserDevice}`.
- Подпись: `X-Remnawave-Signature` = hex HMAC-SHA256 от **сырого тела** с ключом `WEBHOOK_SECRET_HEADER`; также `X-Remnawave-Timestamp`. При ошибке отправки 3 ретрая с интервалом 5 секунд (`BE: src/queue/notifications/webhook-logger/webhook-logger.processor.ts`). В SDK есть `WebhookUtility.validate_webhook(body, signature, secret)` и `parse_webhook`. `parse_webhook` в SDK2 падает на HWID-событиях (issue #63), поэтому эндпоинт должен отвечать 200 даже на событие, которое не удалось разобрать.

Полезные для бота события (`BE: libs/contract/constants/events/events.ts`):
- `user.expired` — подписка истекла → сообщение «продлите»;
- `user.expiration` — напоминания по расписанию: `EXPIRATION_NOTIFICATIONS_ENABLED=true`, `EXPIRATION_NOTIFICATIONS=[-72,-48,-24,24]` (часы до или после `expireAt`, `meta.expiration` со знаком). Заменило `user.expires_in_*` начиная с 2.8.0;
- `user.limited` — трафик кончился (пробный период);
- `user.first_connected` — пользователь впервые подключился (воронка пробного периода);
- `user.not_connected` — не подключился за N часов после создания (`NOT_CONNECTED_USERS_NOTIFICATIONS_*`) → подсказка «как настроить»;
- `user.bandwidth_usage_threshold_reached` — достигнут порог трафика (`BANDWIDTH_USAGE_NOTIFICATIONS_*`);
- `user_hwid_devices.added` / `.deleted` — новое или удалённое устройство (приходят только при включённом HWID-лимите). Попытка сверх лимита **не** создаёт события: при `LIMIT_REACHED` событие не эмитится (`subscription.service.ts`);
- прочее: `user.created/modified/deleted/revoked/disabled/enabled/traffic_reset`, `node.*`, `service.*`, `torrent_blocker.report`.

Без вебхуков всё это можно получать опросом. Вебхуки требуют, чтобы бот принимал входящий HTTPS, а это влияет на тикет 07 (сервер и деплой).

---

## Открытые вопросы
- Какая версия у панели `panel.ts-proxy.com`? От неё зависит выбор между `remnawave==2.8.0` и `remnawave-api==3.x` и переписывание идентификаторов на `id` (тикет 03).
- Включён ли на панели HWID-лимит и какой `fallbackDeviceLimit`? Сейчас bot.py не передаёт `hwid_device_limit`.
- Есть ли доступ к `.env` панели, чтобы включить вебхуки и уведомления об истечении?
