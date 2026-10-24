# TS Proxy — Telegram-бот

Бот продаёт подписку на прокси-сервис TS Proxy и обслуживает клиентов: пробный период, оплата, выдача доступа в Remnawave, напоминания, поддержка, админка.

- Спецификация и задачи: `.scratch/tsproxy-bot/`
- Правила кода: `docs/python-rules.md`
- Словарь терминов: `GLOSSARY.md`

## Запуск на новом Mac

1. Поставить `uv`: `brew install uv`.
2. Скопировать `.env.example` в `.env` и заполнить. Токены в чат и в git не отправлять.
3. Установить зависимости и проверки перед коммитом:
   ```
   uv sync
   uv run pre-commit install
   ```
4. Создать или обновить базу:
   ```
   uv run alembic upgrade head
   ```
5. Запустить (`RUN_MODE=dev` — бот сам снимает webhook и получает сообщения через polling):
   ```
   uv run tsproxy
   ```
   Остановить — `Ctrl+C`: бот доработает текущие сообщения и закроет соединения.

На сервере (`RUN_MODE=prod`) бот при запуске ставит webhook на `PUBLIC_BASE_URL/telegram/webhook`
с секретом `TELEGRAM_WEBHOOK_SECRET`; веб-сервер бота слушает `WEB_HOST:WEB_PORT`.
Внимание: один и тот же бот нельзя одновременно запускать на Mac (polling) и на сервере (webhook) —
запуск на Mac снимет webhook сервера.

## Проверки

```
uv run pytest          # тесты
uv run ruff check .    # ошибки в коде
uv run ruff format .   # оформление
```

Перед каждым коммитом `pre-commit` сам запускает ruff и быстрые тесты.

## Где что лежит

| Папка | Что внутри |
|---|---|
| `content/` | тексты (`texts.yaml`, `admin_texts.yaml`) и тарифы (`tariffs.yaml`) — правятся руками |
| `src/tsproxy/bot` | обработчики Telegram |
| `src/tsproxy/services` | бизнес-логика, ничего не знает про Telegram |
| `src/tsproxy/domain` | чистые правила: сроки, деньги, пробный и льготный период |
| `src/tsproxy/integrations` | Remnawave, платёжка |
| `src/tsproxy/db` | база данных и миграции |
| `src/tsproxy/web` | приём вебхуков и `/health` |
| `src/tsproxy/jobs` | фоновые задачи |
| `ops/` | скрипты сервера (бэкапы панели) |

## Режимы

- `RUN_MODE=dev` — Mac разработчика. Бот трогает только тестовые аккаунты `dev_` в squad `test`.
- `RUN_MODE=prod` — сервер. Бот трогает только свои аккаунты `tg_<telegram_id>`. Клиентов, заведённых в панели вручную, бот не видит и не меняет.
- `PAYMENTS_MODE=fake` — поддельная платёжка для разработки, `real` — настоящая.
