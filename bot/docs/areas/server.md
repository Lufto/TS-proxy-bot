# Запуск, настройки и сервер

## Как устроен процесс

Один процесс, три части (`app.py` — единственное место, где создаются настоящие зависимости):
- **бот aiogram**: на Mac (`RUN_MODE=dev`) — polling, на сервере (`RUN_MODE=prod`) — webhook с секретом;
- **веб-сервер FastAPI**: вебхук Telegram, вебхуки Remnawave, уведомления платёжки, `/health`, `/status`;
- **фоновые задачи**: сводки тревог (1 мин), сверка с Remnawave (5 мин и при запуске), повторы выдачи (1 мин), проверка оплат (2 и 30 мин), сверка с платёжкой (09:00), доделывание возвратов (1 мин), сторож (1 мин), бэкап панели — присмотр (1 ч), бэкап базы (04:30, только prod), отчёт (10:00), пробные и ссылки после сбоя панели (1 мин).

При запуске бот проверяет версию панели: не та основная версия или неверный токен — тревога, 10 минут ожидания и **выход** (systemd перезапустит); панель недоступна — стартует с тревогой.

## Режимы

- `RUN_MODE`: `dev` (Mac, аккаунты `dev_<id>` в squad `test`) или `prod` (сервер, аккаунты `tg_<id>`).
- `PAYMENTS_MODE`: `platega`, `fake` (запрещён в prod), `off` (тарифы видны, купить нельзя, тревога при запуске).

## Где что лежит

| Что | Где |
|---|---|
| Настройки из `.env` (секреты — `SecretStr`) | `config.py` |
| Тексты и тарифы | `content/loader.py`; файлы — `bot/content/{client,admin,alerts}/`, тарифы и ссылки сайта — `config/tariffs.yaml`, `config/site.yaml` |
| База (SQLite): `engine.py`, `models.py`, `repository.py`, миграции `db/migrations/` | `db/` (схема утверждена владельцем; менять — с его согласия) |
| Чистка старых данных | `db/retention.py` (уведомления 90 дней, переписка 3 года), раз в сутки в 05:00 МСК — `jobs/cleanup.py` |
| Логи с маскировкой | `logging_setup.py` |
| Вход: запуск, сборка зависимостей | `__main__.py`, `app.py` |
| Подключение обработчиков бота, общие зависимости, данные кнопок, «не подошло ни одному» | `bot/dispatcher.py`, `bot/context.py`, `bot/callbacks.py`, `bot/fallback.py` |
| Веб-сервер и вебхук Telegram | `web/app.py`, `web/telegram.py` |
| Время и даты: администратору — по Москве, клиенту — по его часам (`<tg-time>`) | `services/clock.py`, `domain/dates.py`, `content/dates.py` |
| Суммы текстом («150 ₽», «100 ⭐») | `content/amounts.py` |
| Сообщения в темы админов (кроме тревог), отправка файлов | `services/admin_topics.py`, `bot/documents.py` |
| Служба и выкатка | `ops/tsproxy-bot/` (`tsproxy.service`, `install.sh`, `deploy.sh`, `rollback.sh`) |
| Сайт | `web/` в корне репозитория, `ops/tsproxy-web/` |

Пути без `ops/` и `config/` — от `bot/src/tsproxy/`.

## Сервер

- Основной сервер — Axion (`ssh tsp`). Служба `tsproxy`, пользователь `tsproxy`. Код — `/opt/tsproxy/app`, `.env` — `/var/lib/tsproxy/.env` (600), база — `/var/lib/tsproxy/data/tsproxy.db`, бэкапы — `/var/backups/tsproxy`.
- Бот слушает адрес шлюза Docker-сети панели; снаружи — через туннель Cloudflare `bot.ts-proxy.com`.
- Сейчас `PAYMENTS_MODE=off`.
- **Любое действие на сервере — только после «ок» владельца на это действие.**

## Тесты

`cd bot && uv run pytest`; проверка стиля — `ruff check` и `ruff format`. Подделки внешних сервисов — `integrations/fakes.py`, `bot/fakes.py`, `services/fakes.py`, `jobs/fakes.py`.
