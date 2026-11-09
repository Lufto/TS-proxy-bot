# Какую подписку Remnawave отдаёт каждому приложению

Type: research
Status: resolved
Blocked by: —

## Question

Remnawave по заголовку приложения (user-agent) отдаёт разный формат подписки: ссылки Xray (base64), Xray JSON, sing-box JSON, mihomo/Clash YAML. Для приложений на ядрах sing-box и mihomo: сохраняются ли в их формате все наши подключения — особенно **xhttp Reality** и xhttp через Cloudflare — или они выпадают / ломаются?

Выяснить по документации и исходникам Remnawave 3.x (наша панель 3.4.x) и ядер:
- какой формат получает каждое приложение из задачи «Рынок бесплатных приложений по устройствам» (по user-agent), и можно ли принудительно отдать другой (правила ответа подписки);
- поддерживает ли sing-box (последняя стабильная) и mihomo (последняя) транспорт xhttp; если нет — что увидит клиент (меньше серверов, ошибка импорта);
- как Remnawave шаблонами (sing-box / mihomo / Xray JSON) может добавить маршрутизацию «Россия напрямую» для таких приложений.

Сервер не трогаем — только чтение документации и кода. Файл — `research/format-podpiski.md`.

## Comments

- 2026-11-09: исследование идёт в фоне, результат — ветка `research/format-podpiski`, файл `.scratch/app-alternatives/research/format-podpiski.md`.

## Answer

2026-11-09. Полный отчёт — ветка `research/format-podpiski`, файл `.scratch/app-alternatives/research/format-podpiski.md` (источники: код Remnawave 3.4.5, mihomo, sing-box).

- **Обычные ссылки (base64), все 8 серверов:** Happ, v2RayTun, Hiddify, Streisand, v2rayNG, v2rayN, Throne, NekoBox.
- **mihomo (Clash Meta) YAML, все 8 серверов:** FlClashX, Clash Verge Rev, Koala Clash, Prizrak-Box. xhttp в mihomo есть с v1.19.22 (2026-04-01); не проверено, хватает ли полей `xhttp-opts` для наших серверов и какая версия mihomo внутри этих приложений.
- **sing-box JSON — теряют 4 xhttp-сервера из 8:** официальные sing-box (SFA/SFI/SFM) — в ядре нет xhttp, не лечится; Karing — Remnawave сам выкидывает xhttp, можно попробовать правило `^karing` → base64/mihomo (не проверено). Stash тоже теряет xhttp.
- **Формат под приложение** задаётся своим правилом ответа подписки по user-agent (выше стандартных).
- **«Россия напрямую»:** mihomo — шаблон с `mode: rule` + наборы правил MetaCubeX `geoip/ru`, `geosite/category-ru`; sing-box — удалённые rule-set; Xray JSON — правило `routing`, но у хостов есть шаблоны `ru-direct` со ссылкой на несуществующий `AX-BALANCER` (исправить до выдачи JSON). `serveJsonAtBaseSubscription` глобально не включать — сломает Happ.
