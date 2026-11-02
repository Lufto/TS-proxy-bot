#!/bin/bash
# Тревоги «диск > 85%» и «памяти < 10%» в тему «Тревоги». План защиты серверов, шаг 7.
# Запуск с Mac владельца (одна команда на сервер):
#   ssh tsp  "bash -s -- 'основной сервер' /etc/remnawave-backup/telegram.env" < ops/server-hardening/install-resource-check.sh
#   ssh tsp2 "bash -s -- 'Германия 2' /etc/tsproxy-alerts/telegram.env" < ops/server-hardening/install-resource-check.sh
# Ставит /usr/local/sbin/tsp-resource-check и запуск по cron каждые 10 минут.
set -euo pipefail
LABEL="$1"
ENV_FILE="$2"
test -f "$ENV_FILE"

cat > /usr/local/sbin/tsp-resource-check <<'SCRIPT'
#!/bin/sh
# Тревога в «Тревоги», если диск занят больше 85% или свободной памяти меньше 10%.
# Пишет один раз при проблеме и один раз, когда всё вернулось. Запуск — cron каждые 10 минут.
HOST_LABEL="__LABEL__"
. __ENV_FILE__
STATE_DIR=/var/lib/tsp-resource-check
mkdir -p "$STATE_DIR"

send() {
  # Токен передаём curl через stdin, чтобы он не был виден в списке процессов.
  printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$BOT_TOKEN" | curl -s --max-time 10 -K - \
    -d chat_id="$CHAT_ID" -d message_thread_id="$ALERTS_THREAD_ID" --data-urlencode text="$1" >/dev/null
}

# check <имя> <плохо: 1/0> <текст тревоги> <текст «всё в норме»>; в файле состояния — 1 или 0
check() {
  prev=$(cat "$STATE_DIR/$1" 2>/dev/null || echo 0)
  [ "$2" = "$prev" ] && return
  if [ "$2" = 1 ]; then send "⚠️ $HOST_LABEL: $3"; else send "✅ $HOST_LABEL: $4"; fi && echo "$2" > "$STATE_DIR/$1"
}

disk=$(df --output=pcent / | tail -1 | tr -dc 0-9)
check disk "$([ "$disk" -gt 85 ] && echo 1 || echo 0)" "диск занят на $disk%" "диск снова в норме ($disk%)"

mem=$(awk '/MemTotal/ {t=$2} /MemAvailable/ {a=$2} END {printf "%d", a * 100 / t}' /proc/meminfo)
check mem "$([ "$mem" -lt 10 ] && echo 1 || echo 0)" "свободно всего $mem% памяти" "память снова в норме (свободно $mem%)"
SCRIPT

sed -i -e "s|__LABEL__|$LABEL|" -e "s|__ENV_FILE__|$ENV_FILE|" /usr/local/sbin/tsp-resource-check
chmod 700 /usr/local/sbin/tsp-resource-check
echo "*/10 * * * * root /usr/local/sbin/tsp-resource-check" > /etc/cron.d/tsp-resource-check
chmod 644 /etc/cron.d/tsp-resource-check
/usr/local/sbin/tsp-resource-check
echo "Готово: проверка диска и памяти каждые 10 минут."
