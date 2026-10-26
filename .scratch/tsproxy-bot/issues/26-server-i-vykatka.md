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
- Из задачи 22: (1) бот читает журнал и список копий бэкапа панели — дать `tsproxy` чтение `/var/lib/remnawave-backup/backup.log` и списка `/var/backups/remnawave/daily/`; `remnawave-backup.sh` каждый раз ставит `chmod 700` на эти папки — поправить скрипт (например, 750 + общая группа), иначе права сбросятся. (2) UptimeRobot: HTTP-монитор `https://bot.…/health` (200 = «ok», 503 = «проблема»). (3) Решить, в какую тему слать ежедневный отчёт (`REPORTS_THREAD_ID`, пусто — общая).
2026-10-26, решения владельца по задаче 22: боту (`tsproxy`) — **только чтение** журнала бэкапов панели и списка копий (правка `chmod` в скрипте бэкапа — на сервере, по шагам с «ок» владельца); бэкапы базы бота шифруются **тем же ключом age, что у панели** (`/etc/remnawave-backup/recipients.txt` — уже так в настройках по умолчанию).
