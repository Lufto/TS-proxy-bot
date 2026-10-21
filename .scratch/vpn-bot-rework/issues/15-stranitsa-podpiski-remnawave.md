# Страница подписки Remnawave

Type: task
Status: resolved
Blocked by: 14

## Question

Развернуть и настроить страницу подписки Remnawave (`remnawave/subscription-page`), чтобы ссылка подписки открывалась в браузере как страница с приложениями и кнопкой «добавить в один клик» для iOS, Android, Windows и macOS. Сейчас её нет. Делаем после обновления панели до 3.x, потому что настройки страницы зависят от версии.

Агент готовит пошаговый чеклист под версию панели (контейнер рядом с панелью, домен или поддомен, переменные окружения, привязка к панели, настройка приложений и брендинга в разделе Subscription). HITL: разворачивает владелец на сервере панели.

## Ход работы

### 2026-10-21 — страница развёрнута (агент по SSH, с «ок» владельца)

- Адрес: `https://sub.ts-proxy.com/<shortUuid>` — маршрут `sub.ts-proxy.com → http://remnawave-subscription-page:3010` в Cloudflare-туннеле `pereezd` (добавил владелец). Мимо Cloudflare не выводили: порт 443 занят Reality; вопрос «подписка мимо Cloudflare» — отдельно, если приложения будут плохо обновляться.
- Контейнер `remnawave-subscription-page` (`remnawave/subscription-page:8.0.0`) в `/opt/remnawave/docker-compose.yml`, сеть `remna-net`, портов наружу нет. Настройки — `/opt/remnawave/subscription.env` (600): `APP_PORT=3010`, `REMNAWAVE_PANEL_URL=http://remnawave:3000`, `REMNAWAVE_API_TOKEN` (токен `subscription-page`, пресет Subpage, 3650 дней). Запуск — `docker-compose up -d --no-deps remnawave-subscription-page` (без `--no-deps` compose v5 пересоздаёт зависимости).
- Бэкапы перед изменением: `/opt/remnawave/docker-compose.yml.bak-subpage`, `.env.bak-subpage`.
- Проверка: в логах «Connected to Remnawave v3.4.5», конфиг страницы загружен; браузер (с `Accept: text/html`) — 200, HTML; v2rayN/Happ/v2RayTun по новому адресу — те же 3 ссылки (хэш = эталон `b474669814fd`). Без `Accept: text/html` браузерная страница не отдаётся — так устроено правило ответа «Browser Subscription» (SRR).
- `SUB_PUBLIC_DOMAIN=sub.ts-proxy.com` (было `panel.ts-proxy.com/api/sub`, копия — `.env.bak-subpage`), панель перезапущена `--no-deps --force-recreate`. Старый и новый адрес отдают Happ и v2RayTun одно и то же (эталон), заголовок `profile-web-page-url` теперь ведёт на `sub.ts-proxy.com`.
- Найдено: заголовок `support-url: https://dummy.docs.rw` — заглушка, приложения показывают её как «поддержку»; заменить на бота поддержки (Subscription → custom response headers). `profile-update-interval: 12` (часов).
- Не сделано: оформление (Subpage Builder); `support-url`; HWID-лимит — включаем вместе с ботом (общий 10 для старых, бот ставит новым личный 3; старых клиентов не трогаем — решение владельца).
- На будущее: при закрытии панели Cloudflare Access оставить открытым `panel.ts-proxy.com/api/sub/*` — по нему обновляются старые ссылки клиентов.
