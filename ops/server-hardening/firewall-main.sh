#!/bin/bash
# Файрвол основного сервера (130.78.12.241). План защиты серверов, шаг 2.
# Запуск с Mac владельца:  ssh tsp 'bash -s' < ops/server-hardening/firewall-main.sh
#
# Через 10 минут файрвол сам выключится (страховка). Если всё работает —
# отменить страховку:      ssh tsp 'systemctl stop ufw-rollback.timer'
# Выключить вручную:       ssh tsp 'ufw disable'   (или в консоли Axion)
set -euo pipefail

# Адреса Cloudflare (https://www.cloudflare.com/ips-v4, 2026-11-02) — только они ходят на 8080 и 2052.
CLOUDFLARE="173.245.48.0/20 103.21.244.0/22 103.22.200.0/22 103.31.4.0/22 141.101.64.0/18
108.162.192.0/18 190.93.240.0/20 188.114.96.0/20 197.234.240.0/22 198.41.128.0/17
162.158.0.0/15 104.16.0.0/13 104.24.0.0/14 172.64.0.0/13 131.0.72.0/22"

export DEBIAN_FRONTEND=noninteractive
command -v ufw >/dev/null || apt-get install -y -qq ufw >/dev/null

ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null

ufw allow 22/tcp comment "SSH" >/dev/null
for port in 443 8443 2053 2083; do
  ufw allow "$port/tcp" comment "клиенты" >/dev/null
done
for net in $CLOUDFLARE; do
  ufw allow from "$net" to any port 8080 proto tcp comment "Cloudflare, домен 1" >/dev/null
  ufw allow from "$net" to any port 2052 proto tcp comment "Cloudflare, домен 2" >/dev/null
done
# Панель живёт в Docker и управляет нодой через порт 2222 этого же сервера.
ufw allow from 172.16.0.0/12 comment "контейнеры Docker" >/dev/null

systemd-run --quiet --unit=ufw-rollback --on-active=600 /usr/sbin/ufw --force disable
ufw --force enable
echo "Файрвол включён. Страховка выключит его через 10 минут — напишите агенту, он проверит."
ufw status | head -12
