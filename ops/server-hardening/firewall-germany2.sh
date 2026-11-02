#!/bin/bash
# Файрвол «Германии 2» (130.78.12.97). План защиты серверов, шаг 2.
# Запуск с Mac владельца:  ssh tsp2 'bash -s' < ops/server-hardening/firewall-germany2.sh
#
# Через 10 минут файрвол сам выключится (страховка). Если всё работает —
# отменить страховку:      ssh tsp2 'systemctl stop ufw-rollback.timer'
# Выключить вручную:       ssh tsp2 'ufw disable'   (или в консоли Axion)
set -euo pipefail

MAIN_SERVER=130.78.12.241  # там панель, она управляет нодой через порт 2222

ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null

ufw allow 22/tcp comment "SSH" >/dev/null
for port in 443 8444; do
  ufw allow "$port/tcp" comment "клиенты" >/dev/null
done
ufw allow from "$MAIN_SERVER" to any port 2222 proto tcp comment "панель -> нода" >/dev/null

systemd-run --quiet --unit=ufw-rollback --on-active=600 /usr/sbin/ufw --force disable
ufw --force enable
echo "Файрвол включён. Страховка выключит его через 10 минут — напишите агенту, он проверит."
ufw status
