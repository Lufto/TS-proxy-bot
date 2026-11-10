#!/bin/bash
# Выкатка сайта (задача сайта 16). Запускает владелец с Mac из корня репозитория:
#   ops/tsproxy-web/deploy.sh
# Собирает web/ из текущего коммита, кладёт на сервер новой папкой релиза, переключает
# ссылку /opt/tsproxy-web/current и перезапускает службу. Хранит 5 последних релизов.
# Откат на прошлый релиз: ops/tsproxy-web/rollback.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [ -n "$(git status --porcelain -- web config)" ]; then
  echo "В web/ или config/ есть незакоммиченные правки — сначала коммит: релиз должен совпадать с коммитом."
  exit 1
fi
REL="$(date -u +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)"

echo "→ Собираю сайт ($REL)"
(cd web && npm run build)
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
cp -R web/.next/standalone/. "$BUILD/"
# Next кладёт в сборку копию web/.env — на сервер её не везём (аудит перед оплатой, 2026-11-11):
# окажись там ключ, он уехал бы в релиз. Сайту во время работы нужна одна переменная —
# брать тексты из apiStatic, а не ходить в несуществующий бэкенд
printf 'HTTP_CLIENT=mock\n' > "$BUILD/.env"
cp -R web/.next/static "$BUILD/.next/static"
cp -R web/public "$BUILD/public"
# Кэш Next — в /var/cache/tsproxy-web (CacheDirectory службы): папка релиза только для чтения
rm -rf "$BUILD/.next/cache"
ln -s /var/cache/tsproxy-web "$BUILD/.next/cache"

echo "→ Кладу релиз на сервер и переключаю"
tar -C "$BUILD" -czf - . \
  | ssh tsp "mkdir -p /opt/tsproxy-web/releases/$REL && tar -C /opt/tsproxy-web/releases/$REL -xzf -"
ssh tsp "bash -s -- $REL" <<'REMOTE' || { echo "Сервер не принял релиз"; exit 1; }
set -euo pipefail
REL="$1"
DIR="/opt/tsproxy-web/releases/$REL"
test -d "$DIR" || { echo "Нет папки $DIR"; exit 1; }
chown -R root:root "$DIR"
# Папка сборки на Mac — из mktemp (права 700): служба tsproxy-web в неё бы не вошла
chmod 755 "$DIR"
ln -sfn "$DIR" /opt/tsproxy-web/current.new
mv -T /opt/tsproxy-web/current.new /opt/tsproxy-web/current
systemctl restart tsproxy-web.service
systemctl start tsproxy-web-proxy.socket
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null http://localhost:8092/; then break; fi
  sleep 1
done
curl -fsS -o /dev/null http://localhost:8092/ || { echo "Сайт не ответил на localhost:8092 — откат: ops/tsproxy-web/rollback.sh"; exit 1; }
curl -fsS -o /dev/null http://172.18.0.1:8091/ || { echo "Вход для туннеля 172.18.0.1:8091 не отвечает"; exit 1; }
# Оставляем 5 последних релизов
ls -1dt /opt/tsproxy-web/releases/* | tail -n +6 | xargs -r rm -rf
echo "Сайт на сервере работает: релиз $REL"
REMOTE

if curl -fsS -o /dev/null --max-time 20 https://ts-proxy.com/; then
  echo "→ Проверяю https://ts-proxy.com"
  (cd web && CHECK_URL=https://ts-proxy.com npm run check)
else
  echo "https://ts-proxy.com пока не открывается — проверьте маршрут туннеля в Cloudflare (ops/tsproxy-web/README.md)"
fi
