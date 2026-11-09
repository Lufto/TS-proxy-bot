#!/bin/bash
# Первая установка сайта на основной сервер (задача сайта 16). Запускает владелец с Mac
# из корня репозитория:  ops/tsproxy-web/install.sh
# Что делает на сервере (ssh tsp): ставит Node.js (официальный архив, сверка SHA-256),
# заводит пользователя tsproxy-web, папку /opt/tsproxy-web и службы. Сайт НЕ запускает —
# это делает первый ops/tsproxy-web/deploy.sh. Откат: ops/tsproxy-web/uninstall.sh.
set -euo pipefail

NODE_VERSION=22.21.0  # та же основная версия, что на Mac (node -v), на ней собирается сайт
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "→ Копирую службы на сервер"
tar -C "$HERE" -cf - tsproxy-web.service tsproxy-web-proxy.socket tsproxy-web-proxy.service \
  | ssh tsp 'mkdir -p /opt/tsproxy-web/units && tar -C /opt/tsproxy-web/units -xf -'

echo "→ Ставлю Node.js $NODE_VERSION, пользователя и службы"
ssh tsp "bash -s -- $NODE_VERSION" <<'REMOTE'
set -euo pipefail
NODE_VERSION="$1"
case "$(uname -m)" in
  x86_64) ARCH=x64 ;;
  aarch64) ARCH=arm64 ;;
  *) echo "Незнакомая архитектура $(uname -m)"; exit 1 ;;
esac
test -x /usr/lib/systemd/systemd-socket-proxyd || { echo "Нет systemd-socket-proxyd"; exit 1; }

NAME="node-v$NODE_VERSION-linux-$ARCH"
if [ ! -x "/opt/tsproxy-web/$NAME/bin/node" ]; then
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  curl -fsSL -o "$TMP/$NAME.tar.xz" "https://nodejs.org/dist/v$NODE_VERSION/$NAME.tar.xz"
  curl -fsSL -o "$TMP/SHASUMS256.txt" "https://nodejs.org/dist/v$NODE_VERSION/SHASUMS256.txt"
  (cd "$TMP" && grep " $NAME.tar.xz\$" SHASUMS256.txt | sha256sum -c -)
  tar -C /opt/tsproxy-web -xJf "$TMP/$NAME.tar.xz"
fi
ln -sfn "/opt/tsproxy-web/$NAME" /opt/tsproxy-web/node
/opt/tsproxy-web/node/bin/node -v

id tsproxy-web >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin tsproxy-web
mkdir -p /opt/tsproxy-web/releases

install -m 644 /opt/tsproxy-web/units/tsproxy-web.service /etc/systemd/system/
install -m 644 /opt/tsproxy-web/units/tsproxy-web-proxy.socket /etc/systemd/system/
install -m 644 /opt/tsproxy-web/units/tsproxy-web-proxy.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable tsproxy-web.service tsproxy-web-proxy.socket >/dev/null
echo "Готово: Node.js, пользователь tsproxy-web и службы установлены (сайт пока не запущен)."
REMOTE
