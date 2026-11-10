# Бэкапы панели Remnawave

Ежедневная зашифрованная копия базы панели, её настроек и настроек ноды. Задача — `.scratch/archive/vpn-bot-rework/issues/22-bekap-i-obnovlenie-remnawave.md`.

## Где что лежит на сервере

| Что | Где |
|---|---|
| Скрипты | `/usr/local/bin/remnawave-backup.sh`, `/usr/local/bin/remnawave-health.sh` |
| Расписание | `/etc/cron.d/remnawave-backup` (бэкап 04:00 МСК, проверка каждые 10 минут) |
| «Замок» шифрования (открытый ключ age) | `/etc/remnawave-backup/recipients.txt` |
| Telegram (необязательно) | `/etc/remnawave-backup/telegram.env`: `BOT_TOKEN`, `CHAT_ID`, `BACKUPS_THREAD_ID`, `ALERTS_THREAD_ID` |
| Копии | `/var/backups/remnawave/daily/` (7 дней), `/var/backups/remnawave/monthly/` (всегда) |
| Журналы и состояние | `/var/lib/remnawave-backup/` (`backup.log`, `health.log`, `last_failure`) |

Старые ежедневные копии удаляются, только если панель проработала без сбоев 7 дней подряд.

## Ключ расшифровки

Секретный ключ — `~/Backups/remnawave/backup-key.txt` на Mac владельца и копия в общем хранилище паролей. На сервере его нет. Без него копии не открыть.

## Как открыть копию

```
age -d -i ~/Backups/remnawave/backup-key.txt remnawave-2026-10-22.tar.gz.age | tar -xzf -
```

Внутри: `db.sql.gz` (дамп PostgreSQL), `panel.env`, `panel-docker-compose.yml`, `subscription.env`, `node/`.

## Установка (как ставили)

```
apt-get install -y age
install -m 700 remnawave-backup.sh remnawave-health.sh /usr/local/bin/
install -m 644 remnawave-backup.cron /etc/cron.d/remnawave-backup
mkdir -p /etc/remnawave-backup && chmod 700 /etc/remnawave-backup
echo "age1…" > /etc/remnawave-backup/recipients.txt
```
