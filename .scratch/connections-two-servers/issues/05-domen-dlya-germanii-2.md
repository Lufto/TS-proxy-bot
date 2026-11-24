# Доменное подключение для Германии 2

Type: grilling
Status: open

## Question

У Германии 2 нет доменного подключения. Решить по образцу основного: поддомен (например `api2.ts-proxy.com` или нейтральный), способ (ws / xhttp, как «домен 1/2»), порт инбаунда на Германии 2, правила в Cloudflare (Origin Rules, SSL), кто что делает (владелец — Cloudflare, агент — инбаунд и хост после «ок»). Плюс: Германия 2 слабая (1 ядро, ~48 Мбит/с) — выдержит ли трафик.

## Черновик инструкции (2026-11-20, по образцу основного)

Основной сейчас: инбаунды `vless_ws` (8080, ws, путь `/stream`) и `vless_xhttp_cf` (2052, xhttp auto, путь `/cf-xh`), оба `security: none`; порты открыты в ufw только для сетей Cloudflare; хосты — `api.ts-proxy.com:443`, TLS, `fp=chrome`, alpn `http/1.1` (ws) и `h2` (xhttp). На Германии 2 порты 8080 и 2052 свободны.

1. Cloudflare (владелец): DNS `A api2 → 130.78.12.97`, оранжевое облако.
2. Cloudflare → Rules → Origin Rules: `api2.ts-proxy.com` + путь начинается с `/stream` → порт 8080; `api2.ts-proxy.com` + путь `/cf-xh` → порт 2052.
3. Cloudflare → Configuration Rules: добавить `api2.ts-proxy.com` в правило «Домен — SSL» (SSL Flexible). WebSockets уже включены на зону.
4. Германия 2, ufw (агент после «ок»): 8080 и 2052 только с сетей Cloudflare (https://www.cloudflare.com/ips-v4), как на основном.
5. Панель (агент после «ок»): в профиль `test` — два инбаунда, копии `vless_ws` / `vless_xhttp_cf` с новыми тегами (`G2_WS_CF`, `G2_XHTTP_CF`); добавить их в `Default-Squad` и `test`. Профиль до изменения — в `~/Backups/`.
6. Хосты «🇩🇪 Германия 2 - подключение домен 1/2»: адрес `api2.ts-proxy.com`, порт 443, TLS, SNI/host `api2.ts-proxy.com`, `fp=chrome`, alpn `http/1.1` / `h2`.
7. Проверка: клиент Xray по ссылкам из подписки — 204, выход `130.78.12.97`; check-host из РФ до `api2.ts-proxy.com:443`.

Запасной вариант — Cloudflare Tunnel (`cloudflared` на Германии 2, публичное имя `api2` → `http://localhost:8080`): наружу не открыт ни один порт, но через туннель надёжно идёт только ws.
