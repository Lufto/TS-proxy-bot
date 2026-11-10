#!/bin/bash
# Проверка панели каждые 10 минут: панель и база запущены, все включённые ноды на связи.
# При сбое записывает время в $STATE/last_failure — по нему remnawave-backup.sh
# решает, можно ли удалять старые копии. Никуда не пишет: тревоги — дело бота.
set -uo pipefail

STATE=/var/lib/remnawave-backup
# Права на $STATE (750, группа бота) ставит remnawave-backup.sh — здесь не трогаем
mkdir -p -m 700 "$STATE"

running() { [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }

problem=""
running remnawave || problem="контейнер remnawave не запущен"
running remnawave-db || problem="контейнер remnawave-db не запущен"
if [ -z "$problem" ]; then
    nodes=$(docker exec remnawave-db sh -c \
        'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select coalesce(bool_and(is_connected), false) from nodes where not is_disabled"' 2>/dev/null)
    [ "$nodes" = t ] || problem="нода не на связи или база не отвечает"
fi

if [ -n "$problem" ]; then
    date +%s > "$STATE/last_failure"
    echo "$(date -Is) сбой: $problem" >> "$STATE/health.log"
fi
