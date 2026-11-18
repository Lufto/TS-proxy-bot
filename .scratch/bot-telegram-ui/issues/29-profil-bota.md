# 29: Профиль бота

Type: task
Status: resolved
Blocked by: 04

**What to build:** аватар, описание («Что умеет бот», с сайтом) и короткое описание («О боте», с сайтом) бот ставит сам (`setMyProfilePhoto`, `setMyDescription`, `setMyShortDescription`); картинку описания в пустом чате владелец загружает в BotFather по пошаговому списку. Сначала @ts_proxybot, боевой — при выкатке. Тексты — на согласование владельцу.

- [ ] Профиль ставится одной командой или скриптом, повторный запуск ничего не ломает
- [ ] Список шагов для BotFather
- [ ] На @ts_proxybot проверено владельцем

## Answer

Код готов 2026-11-18, слит в `ui/telegram-ui` (d3124b0). Команда `cd bot && uv run python scripts/set_bot_profile.py` ставит аватар (`avatar.jpg`, только если аватара нет; `--replace-avatar` — заменить), описание и короткое описание (тексты — `bot/content/client/bot_profile.yaml`); повторный запуск ничего не ломает. Картинку описания владелец загружает в BotFather — шаги в `bot/docs/areas/server.md` «Профиль бота в Telegram». Запуск на @ts_proxybot и проверка — владелец, в задаче 11; боевой бот — в выкатке (12).
