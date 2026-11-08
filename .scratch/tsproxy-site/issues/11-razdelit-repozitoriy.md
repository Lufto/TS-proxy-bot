# Разделить репозиторий на bot/ и web/

Type: task
Status: resolved

## Question

Переложить репозиторий: корень общий для сервиса, бот в `bot/`, сайт в `web/`. Раскладка согласована с владельцем 2026-11-08:

- корень: `CLAUDE.md`, `GLOSSARY.md`, `README.md` (обзор сервиса), `.scratch/`, `docs/` (сервер, ADR, правила агентов), `ops/` (с `ops/tsproxy-bot/`, позже `ops/tsproxy-web/`), `prototypes/`, общий конфиг `config/` (сейчас туда идёт `tariffs.yaml`);
- `bot/`: `src/`, `tests/`, `pyproject.toml`, `uv.lock`, `alembic.ini`, `.env`, `.env.example`, `data/`, `scripts/`, `.pre-commit-config.yaml`, `bot.py`, `rem.py`, `content/` с `texts.yaml` и `admin_texts.yaml`, `bot/docs/` с `python-rules.md` и `bot-backup.md`;
- `site/` пока в корне, удаляется после переключения домена.

Сделать: перенос через `git mv`, путь к тарифам в настройках бота, тесты, ruff и pre-commit проходят; поправить README, `CLAUDE.md` (ссылка на `python-rules.md`), `ops/tsproxy-bot/install.sh` и инструкцию выкатки (на сервер выкладывать только `bot/`, плюс `config/`). Перекладка бота на сервере — чеклист владельцу и выполнение только после «ок». AFK до шага на сервере.

## Answer

2026-11-08: решать здесь нечего, это работа — заменена задачей разработки [dev/01-bot-v-papku-bot.md](../dev/01-bot-v-papku-bot.md).
