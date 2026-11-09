# Выкатка сайта ts-proxy.com и бота с плашкой (задачи сайта 02, 16)

Сайт — собранный Next из `web/` на основном сервере (`ssh tsp`), рядом с ботом. Наружу — через
туннель Cloudflare `pereezd`, как `bot.ts-proxy.com`: адрес сервера через сайт не виден.

```
посетитель → Cloudflare → туннель pereezd → 172.18.0.1:8091 (tsproxy-web-proxy)
                                          → localhost:8092 (tsproxy-web, Next)
сайт в браузере раз в минуту → https://bot.ts-proxy.com/status (бот) → плашка о неполадках
```

Почему два шага (8091 → 8092): Next 16, запущенный не на `localhost`, путает свой адрес,
и переадресация языка уходит в круг. Поэтому Next слушает `localhost`, а туннелю его отдаёт
`systemd-socket-proxyd` (входит в systemd, ставить ничего не надо). Порт 8091 из контейнеров
Docker открыт правилом firewall «контейнеры Docker» (`ops/server-hardening/firewall-main.sh`).

## Порядок первого запуска

Каждую команду запускает владелец с Mac из корня репозитория. Агент объясняет шаг и ждёт «ок».

1. **Бот на Mac остановлен** — с тем же токеном два бота мешают друг другу.
2. **Бот с `/status`** (задача 02): `ops/tsproxy-bot/deploy.sh`. Скрипт кладёт `bot/` и `config/`
   новым релизом, ставит зависимости, обновляет базу, включает и перезапускает службу `tsproxy`,
   показывает `/health` и `/status`. Проверить: меню бота открывается, `/banner` в группе
   администраторов показывает «плашки нет», тревог нет. Откат — `ops/tsproxy-bot/rollback.sh`.
3. **Сайт на сервер, один раз**: `ops/tsproxy-web/install.sh` — Node.js (официальный архив,
   сверка SHA-256), пользователь `tsproxy-web`, службы. Убрать всё — `ops/tsproxy-web/uninstall.sh`.
4. **Сайт**: `ops/tsproxy-web/deploy.sh` — сборка на Mac, релиз на сервер, перезапуск, проверка
   `localhost:8092` и `172.18.0.1:8091` на сервере.
5. **Cloudflare** (руками в панели): Zero Trust → Networks → Tunnels → `pereezd` → Public
   Hostname → Add: домен `ts-proxy.com`, поддомен пустой, тип `HTTP`, адрес `172.18.0.1:8091`.
   То же для поддомена `www`. Если у `ts-proxy.com` уже есть DNS-запись (старый сайт), Cloudflare
   попросит её удалить — удалить.
6. **Проверка снаружи**: `cd web && CHECK_URL=https://ts-proxy.com npm run check`; ссылка
   `https://ts-proxy.com` в Telegram показывает превью; ссылки из бота на `/terms` и `/privacy`
   открываются.
7. **Плашка вживую** (с «ок» владельца): `/banner` → «🛠 Технические работы» — на сайте
   плашка через минуту; снять. Остановить бота (`ssh tsp systemctl stop tsproxy`) — через минуту
   «бот не отвечает»; запустить обратно.

## Обновления

- Сайт: коммит → `ops/tsproxy-web/deploy.sh`. Откат на прошлый релиз — `ops/tsproxy-web/rollback.sh`.
- Бот: коммит → `ops/tsproxy-bot/deploy.sh`. Откат — `ops/tsproxy-bot/rollback.sh`.
- Тексты сайта — `web/apiStatic/`, цены и ссылки — `config/`: после правки выкатить сайт
  (цены — и бота: он читает `config/tariffs.yaml` при запуске).

## Где что на сервере

| Что | Где |
|---|---|
| Node.js | `/opt/tsproxy-web/node` → `node-v22.21.0-linux-…` |
| Релизы сайта | `/opt/tsproxy-web/releases/<время>-<коммит>`, текущий — `/opt/tsproxy-web/current` |
| Кэш Next | `/var/cache/tsproxy-web` |
| Службы | `tsproxy-web.service` (Next), `tsproxy-web-proxy.socket` + `.service` (вход для туннеля) |
| Журнал | `journalctl -u tsproxy-web -u tsproxy-web-proxy` |
| Релизы бота | `/opt/tsproxy/releases/<время>-<коммит>/{app,config}`, ссылки `/opt/tsproxy/app`, `/opt/tsproxy/config` |
