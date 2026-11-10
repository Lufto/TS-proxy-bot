#!/bin/bash
# Установка службы бота и доступ к бэкапам панели (задача 26). Запускает владелец с Mac:
#   ssh tsp 'bash -s' < ops/tsproxy-bot/install.sh
# Бот после этого НЕ запускается — включаем при переключении с Mac (агент скажет).
set -euo pipefail

# Бот только читает журнал и список бэкапов панели (решение 2026-10-26); сами копии остаются 600.
# Скрипт бэкапа из репозитория сам ставит группе tsproxy права 750 при каждом запуске
# (ставится по ops/remnawave-backup/README.md); здесь — чтобы не ждать ночного запуска.
for d in /var/backups/remnawave /var/backups/remnawave/daily /var/backups/remnawave/monthly /var/lib/remnawave-backup; do
  chgrp tsproxy "$d"
  chmod 750 "$d"
done

install -m 644 /opt/tsproxy/ops/tsproxy-bot/tsproxy.service /etc/systemd/system/tsproxy.service
systemctl daemon-reload
systemctl enable tsproxy.service >/dev/null
echo "Готово: доступ к бэкапам выдан, служба tsproxy установлена (пока не запущена)."
