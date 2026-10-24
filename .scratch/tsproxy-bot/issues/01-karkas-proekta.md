# Каркас проекта

Type: task
Status: claimed

## Что сделать

Пустой, но запускаемый проект: `uv`, Python 3.13, пакет `tsproxy` со слоями (`bot`, `services`, `domain`, `integrations`, `db`, `web`, `jobs`, `content`), ruff, `pre-commit` (ruff + быстрые тесты), pytest, настройки из `.env` (`pydantic-settings`), `.env.example` без секретов, загрузка и проверка при запуске `content/texts.yaml`, `admin_texts.yaml`, `tariffs.yaml`. Логирование с маскировкой секретов и ссылок подписки. README: как поставить и запустить на новом Mac. `git init` локально; `bot.py`, `rem.py` остаются в `.gitignore`.

## Готово, когда

- `uv sync` и `uv run pytest` проходят.
- Бот не стартует с понятной ошибкой, если нет текста, тарифа или переменной окружения.
- Автотест: секреты и ссылки подписки не попадают в логи.

Решения: задачи 09, 10, 18 (в `../vpn-bot-rework/issues/`). Спецификация — `../spec.md`.

## Comments
