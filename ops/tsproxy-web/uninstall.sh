#!/bin/bash
# Убрать сайт с сервера целиком (если решили не запускать). Запускает владелец с Mac:
#   ops/tsproxy-web/uninstall.sh
# Маршрут ts-proxy.com в туннеле Cloudflare убирается руками в панели Cloudflare.
set -euo pipefail

ssh tsp 'bash -s' <<'REMOTE'
set -euo pipefail
systemctl disable --now tsproxy-web-proxy.socket tsproxy-web-proxy.service tsproxy-web.service 2>/dev/null || true
rm -f /etc/systemd/system/tsproxy-web.service /etc/systemd/system/tsproxy-web-proxy.socket /etc/systemd/system/tsproxy-web-proxy.service
systemctl daemon-reload
rm -rf /opt/tsproxy-web /var/cache/tsproxy-web
id tsproxy-web >/dev/null 2>&1 && userdel tsproxy-web
echo "Сайт убран с сервера."
REMOTE
