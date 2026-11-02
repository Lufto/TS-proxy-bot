#!/bin/bash
# Установка службы бота и доступ к бэкапам панели (задача 26). Запускает владелец с Mac:
#   ssh tsp 'bash -s' < ops/tsproxy-bot/install.sh
# Бот после этого НЕ запускается — включаем при переключении с Mac (агент скажет).
set -euo pipefail

# Бот только читает журнал и список бэкапов панели (решение 2026-10-26); сами копии остаются 600.
# remnawave-backup.sh при каждом запуске ставит права заново — правим и его.
sed -i 's|^chmod 700 "$DEST" "$DEST/daily" "$DEST/monthly" "$STATE"$|chmod 750 "$DEST" "$DEST/daily" "$DEST/monthly" "$STATE"; chgrp tsproxy "$DEST" "$DEST/daily" "$DEST/monthly" "$STATE"|' /usr/local/bin/remnawave-backup.sh
grep -q 'chgrp tsproxy' /usr/local/bin/remnawave-backup.sh
for d in /var/backups/remnawave /var/backups/remnawave/daily /var/backups/remnawave/monthly /var/lib/remnawave-backup; do
  chgrp tsproxy "$d"
  chmod 750 "$d"
done

install -m 644 /opt/tsproxy/app/ops/tsproxy-bot/tsproxy.service /etc/systemd/system/tsproxy.service
systemctl daemon-reload
systemctl enable tsproxy.service >/dev/null
echo "Готово: доступ к бэкапам выдан, служба tsproxy установлена (пока не запущена)."
