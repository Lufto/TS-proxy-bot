#!/bin/bash
# Откат сайта на прошлый релиз (задача сайта 16). Запускает владелец с Mac:
#   ops/tsproxy-web/rollback.sh
# Переключает /opt/tsproxy-web/current на релиз перед текущим и перезапускает службу.
set -euo pipefail

ssh tsp 'bash -s' <<'REMOTE'
set -euo pipefail
CURRENT="$(readlink -f /opt/tsproxy-web/current)"
PREVIOUS="$(ls -1dt /opt/tsproxy-web/releases/* | grep -vx "$CURRENT" | head -1)"
test -n "$PREVIOUS" || { echo "Прошлого релиза нет — откатываться некуда"; exit 1; }
ln -sfn "$PREVIOUS" /opt/tsproxy-web/current.new
mv -T /opt/tsproxy-web/current.new /opt/tsproxy-web/current
systemctl restart tsproxy-web.service
sleep 3
curl -fsS -o /dev/null http://localhost:8092/ && echo "Откат готов: $(basename "$PREVIOUS")"
# Релиз, с которого откатились, остаётся в папке; следующий deploy.sh его не тронет, пока их меньше 6
REMOTE
