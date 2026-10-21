#!/bin/bash
# Ежедневный зашифрованный бэкап панели Remnawave и ноды.
#
# Хранение:
#   daily/   — копии за последние 7 дней;
#   monthly/ — последняя копия каждого месяца, хранится всегда.
# Старые ежедневные копии удаляются, только если панель проработала без сбоев
# 7 дней подряд (сбои пишет remnawave-health.sh в $STATE/last_failure).
#
# Если есть $CONF/telegram.env, копия уходит в Telegram, а ошибка — в тему тревог.
set -euo pipefail

CONF=/etc/remnawave-backup
STATE=/var/lib/remnawave-backup
DEST=/var/backups/remnawave
KEEP_DAYS=7
HEALTHY_DAYS=7

mkdir -p "$DEST/daily" "$DEST/monthly" "$STATE"
chmod 700 "$DEST" "$DEST/daily" "$DEST/monthly" "$STATE"

log() { echo "$(date -Is) $*" >> "$STATE/backup.log"; }

# Telegram необязателен: без telegram.env функции отправки ничего не делают.
telegram_ready() {
    [ -f "$CONF/telegram.env" ] || return 1
    # shellcheck source=/dev/null
    . "$CONF/telegram.env"
}

send_message() {
    telegram_ready || return 0
    curl -fsS -m 30 "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d chat_id="$CHAT_ID" -d message_thread_id="${ALERTS_THREAD_ID:-}" --data-urlencode text="$1" \
        >/dev/null || log "telegram: сообщение не отправлено"
}

send_file() {
    telegram_ready || return 0
    curl -fsS -m 120 "https://api.telegram.org/bot${BOT_TOKEN}/sendDocument" \
        -F chat_id="$CHAT_ID" -F message_thread_id="${BACKUPS_THREAD_ID:-}" \
        -F document=@"$1" -F caption="$2" \
        >/dev/null || log "telegram: файл не отправлен"
}

on_error() {
    log "FAIL: строка $1"
    send_message "🚨 Бэкап панели Remnawave не получился ($(hostname), строка $1). Журнал: $STATE/backup.log"
}
trap 'on_error $LINENO' ERR

day=$(TZ=Europe/Moscow date +%F)
month=${day:0:7}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
b="$work/remnawave-$day"
mkdir -p "$b"

docker exec remnawave-db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip > "$b/db.sql.gz"
zcat "$b/db.sql.gz" | tail -n 5 > "$work/dump-tail"
grep -q 'PostgreSQL database dump complete' "$work/dump-tail"

cp /opt/remnawave/.env "$b/panel.env"
cp /opt/remnawave/docker-compose.yml "$b/panel-docker-compose.yml"
[ -f /opt/remnawave/subscription.env ] && cp /opt/remnawave/subscription.env "$b/subscription.env"
cp -r /opt/remnanode "$b/node"

out="$DEST/daily/remnawave-$day.tar.gz.age"
tar -C "$work" -czf - "remnawave-$day" | age -R "$CONF/recipients.txt" > "$out.tmp"
mv "$out.tmp" "$out"
cp "$out" "$DEST/monthly/remnawave-$month.tar.gz.age"
chmod 600 "$out" "$DEST/monthly/remnawave-$month.tar.gz.age"

size=$(du -h "$out" | cut -f1)
log "OK: $out ($size)"
send_file "$out" "💾 Бэкап панели Remnawave $day ($size)"

now=$(date +%s)
[ -f "$STATE/last_failure" ] || echo "$now" > "$STATE/last_failure"
last_failure=$(cat "$STATE/last_failure")
if (( now - last_failure >= HEALTHY_DAYS * 86400 )); then
    find "$DEST/daily" -name 'remnawave-*.tar.gz.age' -mtime +$((KEEP_DAYS - 1)) -print -delete \
        | while read -r f; do log "удалён старый: $f"; done
else
    log "чистка пропущена: последний сбой $(date -d "@$last_failure" -Is)"
fi
