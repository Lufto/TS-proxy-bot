# Сервер и выкатка

Type: task
Status: needs-info
Blocked by: 15, 22, 23

## Что сделать

Пользователь `tsproxy`, служба systemd с ограничениями, `.env` 600, веб-сервер для `bot.` и `pay.`, `deploy.sh`, webhook на сервер, UptimeRobot. Изменения на сервере — только по «ок» владельца; не трогать `vombot.py` и `furGPT6.2.py`.
**Ждёт:** задача 26 в `vpn-bot-rework` (защита SSH).

## Готово, когда

- `docs/checklist.md` пройден; `/security-review` без критичных замечаний.

Решения: задачи 07, 21, 26; docs/server-setup.md (в `../vpn-bot-rework/issues/`). Спецификация — `../spec.md`.

## Comments

- Сервер (задача 26): бот работает от `tsproxy`, а `/etc/remnawave-backup/` доступна только root. Положить копию открытого ключа age, читаемую `tsproxy`, в `BACKUP_AGE_RECIPIENTS`; поставить `age`; создать `/var/backups/tsproxy` (владелец `tsproxy`) и добавить в `ReadWritePaths` службы (из задачи 23).
