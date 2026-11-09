#!/bin/bash
# Выкатка бота (задача сайта 02 и «deploy.sh» из задачи 26). Запускает владелец с Mac из корня
# репозитория:  ops/tsproxy-bot/deploy.sh
#
# Раскладка на сервере: /opt/tsproxy/releases/<время>/{app,config} — папки bot/ и config/
# коммита; /opt/tsproxy/app и /opt/tsproxy/config — ссылки на текущий релиз (служба
# не меняется: WorkingDirectory=/opt/tsproxy/app, config_dir ../config).
# Первый запуск убирает старую папку /opt/tsproxy/app (копия всего репозитория до разделения)
# в /opt/tsproxy/app.before-releases — откат: ops/tsproxy-bot/rollback.sh.
#
# Бот на Mac с тем же токеном должен быть остановлен: два бота с одним токеном мешают друг другу.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [ -n "$(git status --porcelain -- bot config)" ]; then
  echo "В bot/ или config/ есть незакоммиченные правки — сначала коммит: релиз должен совпадать с коммитом."
  exit 1
fi
REL="$(date -u +%Y%m%d-%H%M%S)-$(git rev-parse --short HEAD)"

echo "→ Кладу релиз $REL на сервер"
ssh tsp "mkdir -p /opt/tsproxy/releases/$REL"
git archive HEAD bot config \
  | ssh tsp "tar -C /opt/tsproxy/releases/$REL -xf - && mv /opt/tsproxy/releases/$REL/bot /opt/tsproxy/releases/$REL/app"

echo "→ Ставлю зависимости, обновляю базу, переключаю и перезапускаю"
ssh tsp "bash -s -- $REL" <<'REMOTE'
set -euo pipefail
REL="$1"
DIR="/opt/tsproxy/releases/$REL"
UV=/opt/tsproxy-tools/uv
test -x "$UV" || { echo "Нет $UV (задача 26)"; exit 1; }
test -f /var/lib/tsproxy/.env || { echo "Нет /var/lib/tsproxy/.env"; exit 1; }

chown -R root:root "$DIR"
ln -sfn /var/lib/tsproxy/.env "$DIR/app/.env"
# Python бота лежит в /var/lib/tsproxy/python (задача 26); зависимости — строго по uv.lock
(cd "$DIR/app" && UV_PYTHON_INSTALL_DIR=/var/lib/tsproxy/python "$UV" sync --frozen --no-dev --quiet)
# Копия базы перед миграциями (docs/server-setup.md, «Код и деплой»): штатный способ SQLite,
# работает и на живой базе
DB=/var/lib/tsproxy/data/tsproxy.db
if [ -f "$DB" ]; then
  runuser -u tsproxy -- "$DIR/app/.venv/bin/python" -c \
    "import sqlite3, sys; s = sqlite3.connect(sys.argv[1]); d = sqlite3.connect(sys.argv[2]); s.backup(d)" \
    "$DB" "/var/backups/tsproxy/before-deploy-$REL.db"
  echo "Копия базы: /var/backups/tsproxy/before-deploy-$REL.db"
fi
# Миграции — от имени бота: иначе файлы базы станут root и бот не сможет в неё писать
(cd "$DIR/app" && runuser -u tsproxy -- .venv/bin/alembic upgrade head)

# Первый раз: старая папка app (до релизов) уезжает в сторону, на её месте — ссылка
for name in app config; do
  if [ -e "/opt/tsproxy/$name" ] && [ ! -L "/opt/tsproxy/$name" ]; then
    mv "/opt/tsproxy/$name" "/opt/tsproxy/$name.before-releases"
  fi
  ln -sfn "$DIR/$name" "/opt/tsproxy/$name.new"
  mv -T "/opt/tsproxy/$name.new" "/opt/tsproxy/$name"
done

systemctl enable tsproxy.service >/dev/null
systemctl restart tsproxy.service
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null http://172.18.0.1:8090/health; then break; fi
  sleep 1
done
curl -fsS http://172.18.0.1:8090/health && echo
curl -fsS http://172.18.0.1:8090/status && echo
# Оставляем 5 последних релизов
ls -1dt /opt/tsproxy/releases/* | tail -n +6 | xargs -r rm -rf
echo "Бот перезапущен: релиз $REL"
REMOTE

curl -fsS --max-time 20 https://bot.ts-proxy.com/status && echo " ← /status через Cloudflare"
