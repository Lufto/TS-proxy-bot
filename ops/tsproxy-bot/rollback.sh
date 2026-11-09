#!/bin/bash
# Откат бота (задача сайта 02). Запускает владелец с Mac:  ops/tsproxy-bot/rollback.sh
# Переключает /opt/tsproxy/app и /opt/tsproxy/config на релиз перед текущим. Если прошлого
# релиза нет — возвращает старую папку /opt/tsproxy/app.before-releases (до раскладки релизами).
# Миграции базы назад не откатывает: новых с 2026-11-02 нет.
set -euo pipefail

ssh tsp 'bash -s' <<'REMOTE'
set -euo pipefail
CURRENT="$(dirname "$(readlink -f /opt/tsproxy/app)")"
PREVIOUS="$(ls -1dt /opt/tsproxy/releases/* 2>/dev/null | grep -vx "$CURRENT" | head -1 || true)"
if [ -n "$PREVIOUS" ]; then
  for name in app config; do
    ln -sfn "$PREVIOUS/$name" "/opt/tsproxy/$name.new"
    mv -T "/opt/tsproxy/$name.new" "/opt/tsproxy/$name"
  done
  echo "Откат на релиз $(basename "$PREVIOUS")"
elif [ -d /opt/tsproxy/app.before-releases ]; then
  for name in app config; do
    rm -f "/opt/tsproxy/$name"
    if [ -e "/opt/tsproxy/$name.before-releases" ]; then
      mv "/opt/tsproxy/$name.before-releases" "/opt/tsproxy/$name"
    fi
  done
  echo "Откат на старую папку до релизов"
else
  echo "Откатываться некуда"; exit 1
fi
systemctl restart tsproxy.service
sleep 5
curl -fsS http://172.18.0.1:8090/health && echo
REMOTE
