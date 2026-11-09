# Рынок бесплатных приложений по устройствам (ноябрь 2026)

Исследование к задаче `issues/01-rynok-prilozheniy.md`. Проверено 2026-11-09.

## Как читать

- **Ядро** — «движок» внутри приложения, который реально держит соединение. Три семейства: **Xray** (родной для VLESS, понимает всё, что у нас на серверах), **sing-box** (в оригинале **не умеет xhttp**), **mihomo** (бывший Clash Meta; xhttp умеет с весны 2026). От ядра зависит, заработают ли наши xhttp-подключения.
- **xhttp** — наш вид подключения VLESS «через обычный HTTP» (режимы stream-one и packet-up), он же используется в обходе через Cloudflare (xhttp+TLS, h2).
- **Диплинк** — особая ссылка вида `app://...`, которая открывает приложение и сразу добавляет подписку (так работает кнопка «Добавить подписку»).
- **«Россия напрямую» из подписки** — умеет ли приложение брать правила маршрутизации от нас (как Happ/v2RayTun), а не только из своих настроек.
- **Как проверяли:** наличие в российском App Store — через официальный поиск Apple (`itunes.apple.com/lookup?...&country=ru`); если приложения там нет, ответ пустой. Версии и даты — со страниц релизов на GitHub и из магазинов. Google Play открывался **не из России**: то, что страница открывается, не доказывает, что приложение видно с российского аккаунта (помечено «РФ не проверено»).
- **[не проверено]** — факт взят из вторичного источника или не подтверждён, нужно проверить на стенде (задачи 03/05) или у владельца (06).

## Главное, что изменилось (важно для всей карты)

1. **Happ на iPhone/iPad сейчас НЕТ в российском App Store.** Ни основной «Happ - Proxy Utility» (id6504287215), ни «Happ Plus» (id6746188973), ни «Happ Lite» (id6799917773), ни macOS-версия (id6783623643) не находятся с `country=ru`. В российском магазине есть только «Happ for TV» (для Apple TV). История: удаляли в марте и в июне 2026, возвращали под новыми именами, потом снова убирали. В поиске «Happ» в российском App Store — **клоны чужих разработчиков** («Happ Plus VPN ++» от Wesley Perez, «Happ Proxy VPN Plus» от GENNAKER DS и др.). Настоящий Happ выпускает только **Flyfrog LLC**. Источники: Apple lookup (см. ниже); [zona.media 24.06.2026](https://zona.media/news/2026/06/24/happ); [anti-malware 29.06.2026](https://www.anti-malware.ru/news/2026-06-29-111332/50514); [kod.ru, март 2026](https://kod.ru/app-store-happ-v2ray).
2. **v2RayTun на iPhone/iPad тоже нет в российском App Store** (удалён в конце марта 2026 по требованию РКН; id6476628951 сейчас не находится ни в RU, ни в US). Вместе с ним тогда же убрали Streisand и V2Box. Источник: [kod.ru](https://kod.ru/app-store-happ-v2ray), [meduza 28.03.2026](https://meduza.io/news/2026/03/28/apple-udalila-iz-rossiyskogo-app-store-neskolko-vpn-klientov-i-prilozheniy-dlya-obhoda-blokirovok). 27.10.2026 из RU App Store удалили и INCY ([kod.ru](https://kod.ru/incy-removed-ru-app-store), [Moscow Times](https://ru.themoscowtimes.com/2026/10/27/apple-vozobnovila-zachistku-vpn-po-trebovaniyam-roskomnadzora-a209723)).
3. **Happ удалён из Google Play** (страница `com.happproxy` отдаёт 404; по гайду — между 23.10 и 03.11.2026, причина не названа). Для Android теперь только официальный APK с GitHub. Источник: проверка страницы Google Play 2026-11-09; [гайд evilork](https://github.com/evilork/ru-internet-guides/blob/main/guides/happ-removed-google-play.md); [официальный сайт Happ](https://www.happ.su/main/ru).
4. **Hiddify и Streisand на iPhone тоже нельзя поставить с российского аккаунта** — значит, наши клиенты на Hiddify (~4) и Streisand (~2) с iPhone сидят на уже установленных копиях без обновлений (или с иностранным Apple ID).
5. Что **есть** в российском App Store и подходит по смыслу: **OneXray**, **Karing**, **Clash Mi** (бесплатные), Shadowrocket (249 ₽, платный).

Уже установленные приложения продолжают работать, но обновления с российского аккаунта не приходят ([kod.ru](https://kod.ru/app-store-happ-v2ray)).

## Сводка по кандидатам

Даты — последний **стабильный** релиз (если свежее только тестовый — сказано отдельно). «Свежесть ≤3 мес.» = обновлялось после 2026-08-09.

| Приложение | Устройства | Откуда ставить русскому пользователю | Бесплатно? | Ядро | VLESS Reality vision / xhttp / gRPC / ws+TLS / xhttp+TLS | Подписка, диплинк | Последний релиз | Откр. код | Рус. интерфейс | «Россия напрямую» из подписки |
|---|---|---|---|---|---|---|---|---|---|---|
| **Happ** | iOS, Android, Windows, macOS, **Linux есть** (deb/rpm/pkg x64 и arm64), TV | iOS: **нет в RU App Store**, только иностранный Apple ID или TestFlight (ссылки на сайте). Android: APK с GitHub (из Google Play удалён). ПК: GitHub | Да, донаты | Xray | Да (наш текущий основной) | `happ://add/` | iOS 6.3.0 (US store, 02.11.2026); Android 4.9.0 (22.10.2026); ПК 4.7.2 (30.10.2026) | Нет (код закрыт) | Да | Да (уже настроено у нас) |
| **v2RayTun** | iOS, Android, Windows; **Linux нет**; macOS — официальной сборки не нашёл | iOS: **нет в RU App Store**. Android: Google Play 5.26.83 (РФ не проверено). Windows: winget `v2RayTun.Windows` 3.10.15 | Да | Xray | Да (наш запасной) | `v2raytun://import/` [не проверено по оф. докам] | Android 5.26.83; Windows 3.10.15 — дата не проверена | Нет | Да | Да (уже настроено у нас) |
| **OneXray** | iOS/iPadOS 16+, macOS 13+, Android 10+, Windows 10/11, Linux (deb/zip, нужен glibc 2.39+, т.е. Ubuntu 24.04+) | **RU App Store есть** (id6745748773, v26.11.2 от 07.11.2026); Google Play; Microsoft Store; GitHub | Да, без рекламы и аккаунтов | Xray (libXray) | Reality и xhttp — по документации (есть отдельные гайды); gRPC/ws — [не проверено], но ядро Xray это умеет | Подписка по HTTPS-ссылке; **отдельно поддерживает Remnawave** (тип панели «Remnawave», передаёт HWID). Диплинк — **не найден** [не проверено] | 26.11.2 (05.11.2026) | Да, GPL-3.0 | Да (RU в списке языков App Store) | Частично: в режиме «полная конфигурация провайдера» берёт нашу маршрутизацию из Xray JSON целиком; в обычном режиме — свои правила |
| **Karing** | iOS, macOS, Android, Windows, Linux | **RU App Store есть** (id6472431552, но там 1.2.25 от 11.09.2026 — отстаёт от GitHub); Android — только APK с GitHub/сайта (в Google Play нет); ПК — GitHub | Да [есть встроенная реклама «аэропортов»-провайдеров в README; в приложении — не проверено] | Модифицированный sing-box (свой форк с xhttp) | xhttp добавлен и чинился в 1.2.19–1.2.24 (в т.ч. поле `mode`); Reality — да; gRPC/ws — да (sing-box) ; режимы stream-one/packet-up — [не проверено] | `karing://install-config?url=` (упомянут в релизе 1.2.22.2502) | 1.2.26.2923 (09.11.2026) | Частично (README; лицензия не указана) | [не проверено] — в App Store указан только английский | Свои правила/наборы; из подписки — [не проверено] |
| **Clash Mi** (тот же разработчик, что Karing) | iOS, macOS, Android, Windows, Linux | **RU App Store есть** (id6744321968, 1.0.31 от 17.10.2026); остальное — GitHub/сайт | Да | mihomo | mihomo: xhttp с v1.19.22 (апр. 2026), Reality, gRPC, ws — да; режимы xhttp — [не проверено] | `clash://install-config?...&url=` (так в стандартном списке Remnawave) | 1.0.32.1803 (03.11.2026) | Да (GitHub KaringX/clashmi) | [не проверено] — в App Store только английский | Да, если Remnawave отдаёт Clash/mihomo-конфиг с правилами (правила едут внутри конфига) |
| **Hiddify** | Android, Windows, macOS, Linux (AppImage/deb); iOS — **нет в RU App Store** | Android: Google Play 5.5.5 (РФ не проверено) + APK GitHub; ПК: GitHub | Да | Новое «переработанное» ядро 5.5 (на базе sing-box, Hiddify-форк) | В 5.5.x **добавлен XHTTP**; Reality/gRPC/ws — да; режимы xhttp — [не проверено] | `hiddify://import/<ссылка>#имя` (официальная дока) | 5.5.5 (04.11.2026) | Да (своя лицензия) | Да (есть `ru.i18n.json`) | Нет: из заголовков подписки берёт только DNS, название, трафик и т.п. |
| **v2rayNG** | Android | Только APK с GitHub (в Google Play нет — страница 404) | Да | Xray (обновлён до 26.10.31) | Да, всё (Xray) | `v2rayng://install-config?name=...&url=` | 2.3.15 (01.11.2026) | Да, GPL-3.0 | Да | Нет (свои пресеты маршрутизации) [не проверено] |
| **v2rayN** | Windows, macOS, Linux (deb/rpm) | GitHub | Да | Xray (26.10.10) + sing-box 1.14 + mihomo | Да, всё (через Xray) | По ссылке; диплинк — [не найден] | 7.25.6 (11.10.2026) | Да, GPL-3.0 | Да (`ResUI.ru.resx`) | Нет [не проверено] |
| **Throne** (наследник NekoRay) | Windows, Linux, macOS | GitHub | Да | sing-box + Xray | xhttp — через Xray (в 1.4.1 чинили XHTTP Extra); всё остальное — да | `throne://addsub/<base64(ссылка#имя)>` (официальная дока) | 1.4.1 (06.11.2026) | Да, GPL-3.0 | Да (`ru_RU.ts`) | Нет [не проверено] |
| **FlClashX** | Android, Windows, macOS, Linux | GitHub (и Obtainium) | Да, донаты | mihomo (оригинальный, 1.19.28+) | mihomo — xhttp есть | `flclashx://install-config?url=`; заточен под Remnawave (HWID, анонсы, кнопки «продлить») | Стабильный **0.4.2 от 18.07.2026 (старше 3 мес.)**; тестовые 0.5.0-pre.7 от 03.11.2026 | Да, GPL-3.0 | Да | Да, через Clash/mihomo-конфиг из Remnawave [не проверено] |
| **Koala Clash** | Windows, macOS, Linux | GitHub (coolcoala/koala-clash) | Да | mihomo | mihomo — xhttp есть | `koala-clash://install-config?url=` (список Remnawave) | 1.5.0 (09.10.2026) | Да, GPL-3.0 | [не проверено] | Через mihomo-конфиг [не проверено] |
| **Clash Verge Rev** | Windows, macOS, Linux | GitHub | Да | mihomo | mihomo — xhttp есть | `clash://install-config?url=` | 2.5.10 (03.11.2026) | Да, GPL-3.0 | [не проверено] | Через mihomo-конфиг [не проверено] |
| **Prizrak-Box** | Windows, macOS, Linux, Android; iOS — только TestFlight по ссылке из Telegram | GitHub | Да | Prizrak-Core (форк mihomo) | xhttp — «улучшения XHTTP» в rc02 | `prizrak-box://install-config?url=` | **Только тестовые**: 2.0.0-rc02 (08.11.2026); стабильного свежего нет | Да | Да (русский автор, legiz-ru) [интерфейс не проверен] | Через mihomo-конфиг [не проверено] |

Плюс на заметку, но не в таблицу кандидатов: **V2RC** (Lukayn LTD, есть в RU App Store, русский язык, есть встроенные покупки — код закрыт, протоколы не проверены), **sing-box MT**, **v2RAGE**, **UXWay** и т.п. — неизвестные закрытые приложения из RU App Store; без проверки рекомендовать нельзя.

## Рекомендуемый короткий список по устройствам

Принцип: сначала то, что русский пользователь ставит без иностранного Apple ID и без APK «из интернета»; потом то, чем клиенты уже пользуются; ядро Xray предпочтительнее (понимает все наши подключения без оговорок).

### iPhone / iPad
1. **OneXray** — единственное найденное бесплатное приложение на **Xray** в российском App Store, свежее (07.11.2026), открытый код, русский язык, прямо поддерживает Remnawave. Главный кандидат на «основное для новых клиентов на iPhone».
2. **Happ** — оставить для тех, у кого уже стоит, и для владельцев иностранного Apple ID/TestFlight. Для новых клиентов с российским аккаунтом — **не ставится** без танцев.
3. **Karing** — в RU App Store, xhttp добавлен в собственный форк sing-box. Минус: версия в App Store отстаёт (сентябрь), русского языка в магазине не заявлено.
4. **Clash Mi** — в RU App Store, ядро mihomo (xhttp есть). Минус: работает через Clash-конфиг, надо проверить, что Remnawave отдаёт xhttp в этом формате (задача 02).

v2RayTun на iPhone в список для новых клиентов **не попадает** (не ставится с российского аккаунта) — только «если уже стоит».

### Android
1. **Happ** (APK с GitHub) — основное, но теперь только файлом; на сайте/в боте нужна прямая ссылка на официальный APK.
2. **v2RayTun** (Google Play) — запасное, ставится из магазина.
3. **Hiddify** (Google Play) — им уже пользуются ~4 клиента, в 5.5 появился xhttp; минус — «Россия напрямую» из подписки не возьмёт.
4. **OneXray** (Google Play) — Xray, открытый код, одно и то же приложение на всех устройствах.

Запас: v2rayNG (APK, Xray, русский — для тех, кто уже на нём).

### Windows
1. **Happ** — GitHub, x64 и arm64.
2. **v2RayTun** — условно: есть в winget (3.10.15), но дату последнего релиза и официальную страницу загрузки подтвердить не удалось; проверить в задаче 05.
3. **Hiddify** — свежий, xhttp есть, им уже пользуются.
4. **v2rayN** — Xray, русский, очень активный; минус — интерфейс сложнее для новичка.

Запас: OneXray (Microsoft Store), Throne.

### macOS
1. **Happ** — DMG с GitHub (macOS-версии в RU App Store нет).
2. **OneXray** — Mac App Store (тот же id, что и iOS, — есть в RU).
3. **Hiddify** — DMG/PKG с GitHub.
4. **Karing** — Mac App Store RU или DMG.

Запас: v2rayN (DMG), Clash Verge Rev. v2RayTun для macOS официально не найден.

### Linux
1. **Happ** — **да, у Happ есть Linux** (deb, rpm, pkg.tar.zst для x64 и arm64).
2. **Hiddify** — AppImage/deb.
3. **v2rayN** — deb/rpm, Xray, русский.
4. **Throne** — Xray+sing-box, русский, свой диплинк.

Запас: OneXray (требует свежий glibc 2.39+ — на старых Ubuntu/Debian не запустится), FlClashX. **У v2RayTun Linux-версии нет.**

## Отброшенные

| Приложение | Почему |
|---|---|
| **sing-box (SFI / SFA / SFM)** | Оригинальный sing-box не умеет xhttp (в релиз-нотах до 1.15-alpha нет ни слова); iOS-версии нет в RU App Store. |
| **NekoBox for Android** | Последний релиз 1.4.2 от 09.02.2026 (старше 3 мес.); ядро sing-box без xhttp; в Google Play нет. |
| **Streisand** | Только iPhone/Mac; **удалён из RU App Store** в марте 2026 (в US есть, 1.7.0 от 01.11.2026). Можно упомянуть «если уже установлен». |
| **V2Box** | Удалён из RU App Store в марте 2026. |
| **FoXray** | Не нашёл ни в RU, ни в US App Store (id6448898396 пустой); в RU под этим именем — посторонний «Foxray VPN» другого разработчика. |
| **V2Guard** | Под id6752994543 сейчас «V2Ray VPN Client» (O & O PROFESSIONALS LLC): нет в RU App Store, не обновлялся с 20.05.2026, по описанию — платные недельные/годовые подписки. Что именно у наших 1–2 клиентов под этим именем — [не проверено], уточнить у них. |
| **Shadowrocket** | Платный: 249 ₽ в RU App Store. |
| **Stash** | Платный: 499 ₽ в RU App Store. |
| **INCY** | Удалён из RU App Store 27.10.2026. |
| **FlClashX** (в основной список) | Стабильная версия старше 3 месяцев, свежие только тестовые 0.5.0-pre; в запасе — хорош для Remnawave, вернуть, когда выйдет 0.5.0. |
| **Prizrak-Box** | Только тестовые сборки (2.0.0-rc), iOS через TestFlight по ссылке из Telegram — «с танцами». |
| **Koala Clash, Clash Verge Rev** | Только ПК, ядро mihomo — работают, но по всем пунктам уступают Happ/Hiddify/v2rayN; в запасе. |
| **Клоны «Happ Plus VPN ++», «Happ Proxy VPN Plus», «Happ Plus VPN: Тест скорости» и т.п.** | Чужие разработчики, выдают себя за Happ. Нельзя рекомендовать; стоит предупредить клиентов. |

## Риски

- **Российский App Store нестабилен.** За 2026 год Happ, v2RayTun, Streisand, V2Box, INCY удалены по требованию РКН; удаления идут волнами (март, июнь, август–сентябрь, октябрь). OneXray, Karing и Clash Mi могут пропасть так же — список для iPhone надо перепроверять регулярно (пункт «Повторная проверка» в карте).
- **Google Play тоже чистит**: Happ пропал в конце октября 2026. Доступность Google Play-приложений именно с российского аккаунта в этом исследовании не проверялась (страницы открывались не из РФ).
- **xhttp в не-Xray приложениях** (Hiddify, Karing, Clash Mi и прочие на mihomo) появился недавно; поддерживаются ли **оба** режима (stream-one и packet-up) и вариант через Cloudflare — проверять на стенде. Для mihomo-приложений ещё и вопрос, отдаёт ли Remnawave xhttp в Clash-формате (задача 02).
- **«Россия напрямую»** из подписки гарантированно работает только в Happ и v2RayTun; у OneXray — только в режиме «полная конфигурация провайдера» (Xray JSON); у Hiddify — нет.
- **Уязвимость «локальный SOCKS5 без пароля»**: весной 2026 исследователь сообщал, что Hiddify, v2rayNG, NekoBox, Happ и др. поднимают на телефоне локальный прокси без авторизации, через который другие приложения могут узнать IP сервера. Исправлено ли сейчас — [не проверено]. Источники: [securitylab](https://www.securitylab.ru/news/571288.php), [kod.ru 09.07.2026](https://kod.ru/vpn-happ-vulnerability), [ntc.party](https://ntc.party/t/%D0%BD%D0%B0%D0%B9%D0%B4%D0%B5%D0%BD%D0%B0-%D0%BA%D1%80%D0%B8%D1%82%D0%B8%D1%87%D0%B5%D1%81%D0%BA%D0%B0%D1%8F-%D1%83%D1%8F%D0%B7%D0%B2%D0%B8%D0%BC%D0%BE%D1%81%D1%82%D1%8C-vless-%D0%BA%D0%BB%D0%B8%D0%B5%D0%BD%D1%82%D0%BE%D0%B2/23871/1).
- **Стандартный список приложений Remnawave устарел**: в `app-config.json` страницы подписки до сих пор ссылки на удалённые Happ Plus (RU) и Streisand (RU). Если наша страница подписки берёт ссылки оттуда — они ведут в никуда.
- **RuStore**: ни одного из кандидатов там не нашёл (источников нет) [не проверено].

## Источники

Магазины (официальный API Apple, запрос 2026-11-09, `country=ru` и `country=us`):
- Happ: https://itunes.apple.com/lookup?id=6504287215&country=ru (пусто), US: https://apps.apple.com/us/app/happ-proxy-utility/id6504287215 ; Happ Plus id6746188973, Happ Lite id6799917773, Happ macOS id6783623643 — пусто в RU; Happ for TV в RU есть: https://apps.apple.com/ru/app/happ-proxy-utility-for-tv/id6748297274 ; все приложения Flyfrog LLC: https://itunes.apple.com/lookup?id=1729134548&entity=software&country=ru
- v2RayTun id6476628951 — пусто в RU и US.
- Streisand id6450534064, Hiddify id6596777532, V2Box id6446814690, FoXray id6448898396, sing-box id6673731168, V2Guard id6752994543 — пусто в RU.
- OneXray: https://apps.apple.com/ru/app/onexray/id6745748773
- Karing: https://apps.apple.com/ru/app/karing/id6472431552
- Clash Mi: https://apps.apple.com/ru/app/clash-mi/id6744321968
- Shadowrocket 249 ₽: https://apps.apple.com/ru/app/shadowrocket/id932747118 ; Stash 499 ₽: https://apps.apple.com/ru/app/stash-rule-based-proxy/id1596063349
- V2RC: https://apps.apple.com/ru/app/v2rc-%D0%BA%D0%BB%D0%B8%D0%B5%D0%BD%D1%82-vless-%D0%B8-vmess/id6748362312
- Google Play (открыто не из РФ): v2RayTun https://play.google.com/store/apps/details?id=com.v2raytun.android (5.26.83); Hiddify https://play.google.com/store/apps/details?id=app.hiddify.com (5.5.5); OneXray https://play.google.com/store/apps/details?id=net.yuandev.onexray (26.11.2); sing-box https://play.google.com/store/apps/details?id=io.nekohasekai.sfa ; 404: `com.happproxy`, `com.v2ray.ang`, `moe.nb4a`.
- winget v2RayTun: https://github.com/microsoft/winget-pkgs/tree/master/manifests/v/v2RayTun/Windows

Официальные сайты и документация:
- Happ, все платформы и ссылки: https://www.happ.su/main/ru
- OneXray: https://github.com/OneXray/OneXray , Remnawave: https://onexray.com/docs/servers/remnawave/ , импорт: https://onexray.com/docs/servers/import/
- Hiddify, диплинк и заголовки: https://hiddify.com/app/URL-Scheme/
- Throne, диплинк: https://throneproj.github.io/advanced/deeplinks/
- Karing README: https://github.com/KaringX/karing ; Clash Mi README: https://github.com/KaringX/clashmi
- FlClashX README (Remnawave-заголовки): https://github.com/pluralplay/FlClashX
- v2rayN, файлы релиза по платформам: https://github.com/2dust/v2rayN/wiki/Release-files-introduction
- Диплинки из стандартного списка Remnawave: https://github.com/remnawave/subscription-page/blob/main/frontend/public/assets/app-config.json

GitHub-релизы (даты и тексты релизов):
- Happ desktop: https://github.com/Happ-proxy/happ-desktop/releases (4.7.2 от 30.10.2026, есть Linux deb/rpm/pkg); Happ Android: https://github.com/Happ-proxy/happ-android/releases
- Hiddify 5.5.5 (XHTTP в «переработанном ядре»): https://github.com/hiddify/hiddify-app/releases/tag/v5.5.5
- v2rayNG 2.3.15: https://github.com/2dust/v2rayNG/releases/tag/2.3.15 ; v2rayN 7.25.6: https://github.com/2dust/v2rayN/releases/tag/7.25.6
- Karing (xhttp в 1.2.19/1.2.21/1.2.24, `karing://install-config` в 1.2.22.2502): https://github.com/KaringX/karing/releases
- Clash Mi: https://github.com/KaringX/clashmi/releases
- OneXray 26.11.2: https://github.com/OneXray/OneXray/releases
- Throne 1.4.1: https://github.com/throneproj/Throne/releases
- FlClashX: https://github.com/pluralplay/FlClashX/releases
- Koala Clash 1.5.0: https://github.com/coolcoala/koala-clash/releases
- Clash Verge Rev 2.5.10: https://github.com/clash-verge-rev/clash-verge-rev/releases
- Prizrak-Box: https://github.com/legiz-ru/Prizrak-Box/releases
- NekoBox 1.4.2 (09.02.2026): https://github.com/MatsuriDayo/NekoBoxForAndroid/releases
- sing-box (нет xhttp; 1.14.4 от 24.10.2026): https://github.com/SagerNet/sing-box/releases
- mihomo (xhttp с 1.19.22, апрель 2026): https://github.com/MetaCubeX/mihomo/releases
- Xray-core 26.11.9: https://github.com/XTLS/Xray-core/releases

Новости об удалениях (вторичные, но с письмами Apple/разработчиков):
- https://kod.ru/app-store-happ-v2ray
- https://kod.ru/incy-removed-ru-app-store
- https://zona.media/news/2026/06/24/happ
- https://www.anti-malware.ru/news/2026-06-29-111332/50514
- https://meduza.io/news/2026/03/28/apple-udalila-iz-rossiyskogo-app-store-neskolko-vpn-klientov-i-prilozheniy-dlya-obhoda-blokirovok
- https://ru.themoscowtimes.com/2026/10/27/apple-vozobnovila-zachistku-vpn-po-trebovaniyam-roskomnadzora-a209723
- https://github.com/evilork/ru-internet-guides/blob/main/guides/happ-removed-google-play.md
