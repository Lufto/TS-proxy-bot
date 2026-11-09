# Рынок бесплатных приложений по устройствам

Type: research
Status: resolved
Blocked by: —

## Question

Какие бесплатные приложения для VLESS-подписок сейчас (ноябрь 2026) реально доступны русскому пользователю на iPhone/iPad, Android, Windows, macOS и Linux — и какие из них проходят обязательные требования карты (см. Notes в `../map.md`)?

По каждому кандидату (и заодно по Happ и v2RayTun — особенно есть ли у них Linux):
- устройства, где есть; откуда ставить (российский App Store / Google Play / RuStore / официальный файл), ссылки;
- бесплатно ли целиком, есть ли реклама / платный «премиум» и что под ним;
- поддерживает ли VLESS Reality tcp+vision, **xhttp** (stream-one и packet-up), gRPC, ws+TLS, xhttp+TLS через Cloudflare; на каком ядре (Xray / sing-box / mihomo) и какой версии;
- импорт подписки: по ссылке, диплинк-схема для кнопки «Добавить подписку» (вида `app://import/...`);
- дата последнего релиза, открытый код, русский интерфейс;
- умеет ли получать правила маршрутизации из подписки (для «Россия напрямую»).

Кандидаты для начала (не ограничиваться): Hiddify, Streisand, v2rayNG, v2rayN, V2Guard, NekoBox, Karing, FlClashX, Clash Verge Rev, Koala Clash, Prizrak-Box, Throne, FoXray, Shadowrocket/Stash (платные — отметить и отбросить), sing-box (SFI/SFA/SFM).

Итог: таблица по устройствам + короткий список отброшенных с причиной. Файл — `research/rynok-prilozheniy.md`.

## Comments

- 2026-11-09: исследование идёт в фоне, результат — ветка `research/rynok-prilozheniy`, файл `.scratch/app-alternatives/research/rynok-prilozheniy.md`.

## Answer

2026-11-09. Полный отчёт — ветка `research/rynok-prilozheniy`, файл `.scratch/app-alternatives/research/rynok-prilozheniy.md`.

**Главное:** в российском App Store нет ни Happ (основное, Plus, Lite, Mac — есть только Happ TV; при поиске «Happ» — подделки), ни v2RayTun (убран в марте 2026), ни Streisand, V2Box, Hiddify. Happ пропал и из Google Play (между 23.10 и 03.11.2026) — на Android только APK с GitHub. Перепроверено основной сессией через Apple lookup `country=ru` (0 результатов для обоих id; Happ есть в `country=us`) и Google Play (404).

**Предварительный список по устройствам:**
- **iPhone/iPad:** OneXray (есть в российском App Store, ядро Xray, открытый код, русский, поддержка Remnawave) → Happ (только у кого уже стоит / иностранный Apple ID / TestFlight) → Karing, Clash Mi (в российском магазине; xhttp появился недавно; Karing получает от Remnawave sing-box и теряет xhttp — см. задачу 02). v2RayTun — только у кого уже стоит.
- **Android:** Happ (APK), v2RayTun (Google Play 5.26.83), Hiddify 5.5.5 (xhttp с 5.5), OneXray; запас — v2rayNG (APK).
- **Windows:** Happ, v2RayTun (под вопросом: winget 3.10.15, официальной страницы скачивания не нашли), Hiddify, v2rayN; запас — OneXray, Throne.
- **macOS:** Happ (DMG), OneXray (российский Mac App Store), Hiddify, Karing. Официального v2RayTun для Mac не нашли.
- **Linux:** Happ (deb/rpm, x64 и arm64), Hiddify, v2rayN, Throne. v2RayTun для Linux нет. OneXray — от Ubuntu 24.04.

**Отброшены:** Streisand, V2Box, INCY, FoXray (не установить из России); Shadowrocket, Stash (платные); официальный sing-box, NekoBox (нет xhttp); V2Guard (нет в российском магазине, платные подписки); Prizrak-Box (только тестовые сборки). FlClashX — запас.

**Риски:** App Store в России удаляет такие приложения волнами (март, июнь, авг–сен, окт) — список нужно пересматривать; доступность Google Play с российского аккаунта не проверена; xhttp в не-Xray приложениях новый — проверять на стенде; «Россия напрямую» из подписки умеют только Happ и v2RayTun (OneXray — только в режиме полного конфига, Hiddify — нет); в стандартном списке приложений Remnawave ссылки на удалённые Happ Plus и Streisand; весной 2026 сообщали о локальном прокси без пароля в Hiddify, v2rayNG, NekoBox, Happ (не подтверждено, исправлено ли). Диплинков импорта не нашли для OneXray и v2rayN.
