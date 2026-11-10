# Сервер и выкатка

Type: task
Status: in-progress
Blocked by: 15, 22, 23

## Что сделать

Пользователь `tsproxy`, служба systemd с ограничениями, `.env` 600, веб-сервер для `bot.` и `pay.`, `deploy.sh`, webhook на сервер, UptimeRobot. Изменения на сервере — только по «ок» владельца; не трогать `vombot.py` и `furGPT6.2.py`.
**Ждёт:** задача 26 в `vpn-bot-rework` (защита SSH).

## Готово, когда

- `docs/checklist.md` пройден; `/security-review` без критичных замечаний.

Решения: задачи 07, 21, 26; docs/server-setup.md (в `../vpn-bot-rework/issues/`). Спецификация — `../spec.md`.

## Comments

- Сервер (задача 26): бот работает от `tsproxy`, а `/etc/remnawave-backup/` доступна только root. Положить копию открытого ключа age, читаемую `tsproxy`, в `BACKUP_AGE_RECIPIENTS`; поставить `age`; создать `/var/backups/tsproxy` (владелец `tsproxy`) и добавить в `ReadWritePaths` службы (из задачи 23).
- Из задачи 22: (1) бот читает журнал и список копий бэкапа панели — дать `tsproxy` чтение `/var/lib/remnawave-backup/backup.log` и списка `/var/backups/remnawave/daily/`; `remnawave-backup.sh` каждый раз ставит `chmod 700` на эти папки — поправить скрипт (например, 750 + общая группа), иначе права сбросятся. (2) UptimeRobot: HTTP-монитор `https://bot.…/health` (200 = «ok», 503 = «проблема»). (3) Решить, в какую тему слать ежедневный отчёт (`REPORTS_THREAD_ID`, пусто — общая).
2026-10-26, решения владельца по задаче 22: боту (`tsproxy`) — **только чтение** журнала бэкапов панели и списка копий (правка `chmod` в скрипте бэкапа — на сервере, по шагам с «ок» владельца); бэкапы базы бота шифруются **тем же ключом age, что у панели** (`/etc/remnawave-backup/recipients.txt` — уже так в настройках по умолчанию).

- 2026-10-31, владелец: сервер настраивает агент. Берём **новый чистый VPS** и переносим туда всё с Zomro: Remnawave (панель, нода, WARP, туннель cloudflared), старых ботов (`vombot.py`, `furGPT6.2.py`) и новый бот. Размер: 1 ядро / 2 ГБ не хватит (у Remnawave минимум 2 ядра / 2 ГБ только на панель; на Zomro сейчас занято ~2,6 ГБ из 4,8, данные 2026-10-21). Нужно 2 ядра / 4 ГБ / 40+ ГБ диска. Риск: у ноды сменится IP. Проверить, чем адрес ноды задан в профилях (IP или домен), до переезда. Старый Zomro не гасить, пока клиенты не проверены на новом.

## Выкатка 2026-11-02

Бот работает на основном сервере (`ssh tsp`, Axion `130.78.12.241`), служба `tsproxy` (systemd, пользователь `tsproxy`, ограничения — `ops/tsproxy-bot/tsproxy.service`).
- Код — `/opt/tsproxy/app` (копия коммита `ed51bb8` через `git archive`, без `.scratch`, `site`, `prototypes`); Python 3.13 для бота — `/var/lib/tsproxy/python` (uv в `/opt/tsproxy-tools`); зависимости — `uv sync --frozen --no-dev`.
- `.env` — `/var/lib/tsproxy/.env` (600, ссылка из `/opt/tsproxy/app/.env`); база — `/var/lib/tsproxy/data/tsproxy.db` (новая, с Mac не переносили); бэкапы — `/var/backups/tsproxy`, ключ age — `/var/lib/tsproxy/backup-recipients.txt`.
- Слушает `172.18.0.1:8090` (шлюз сети Docker `remnawave_remna-net`): туннель Cloudflare `pereezd` → `bot.ts-proxy.com` (маршрут добавил владелец), вебхуки панели — `WEBHOOK_URL=http://172.18.0.1:8090/remnawave/webhook` в `/opt/remnawave/.env` (копия до правки — `/root/remnawave-env-before-webhook.bak`).
- **`PAYMENTS_MODE=off`**: владелец выбрал тестовый кабинет Platega, но в prod он запрещён (аудит R08 — платежи тестового кабинета без денег). Включить: боевые `PLATEGA_MERCHANT_ID`/`PLATEGA_SECRET`, `PAYMENTS_MODE=platega`, `systemctl restart tsproxy`; Callback URL в кабинете — `https://bot.ts-proxy.com/payments/notification`.
- Права: бот читает журнал и список бэкапов панели (`ops/tsproxy-bot/install.sh`, запускал владелец — правки на сервере агенту блокирует классификатор).
- Проверено: `/health` 200 через Cloudflare, webhook Telegram, вебхук панели `service.panel_started` принят, ноды на связи.
- Осталось: `deploy.sh` для обновлений, UptimeRobot, проверки «После выкатки» из `docs/checklist.md`, `/security-review`; токены не перевыпускали (владелец, 2026-11-02); токен туннеля — перевыпустить (засвечен в сессии агента 2026-11-02).
