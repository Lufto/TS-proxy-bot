# Какую подписку Remnawave отдаёт каждому приложению

Исследование к задаче `issues/02-format-podpiski-remnawave.md`. Дата: 2026-11-09. Сервер и панель не трогали: только документация и исходный код.

Источники проверены по коду Remnawave версии **3.4.5** (тег `3.4.5` в `remnawave/backend`, такая версия стоит у нас; в 3.4.8 эти файлы не менялись). Пометка **[не проверено]** значит, что вывод сделан по описанию или логике, а не по коду или документации.

## Словарик

- **Формат подписки.** По одной и той же ссылке панель отдаёт разное содержимое, смотря кто спрашивает:
  - **Xray base64.** Просто список ссылок `vless://…`, упакованный в base64 (способ записать текст «безопасными» символами). Маршрутизации внутри нет.
  - **Xray JSON.** Готовый файл настроек ядра Xray, отдельный на каждый сервер. В нём можно прописать маршрутизацию.
  - **sing-box JSON.** Файл настроек ядра sing-box, все серверы в одном файле.
  - **mihomo YAML.** Файл настроек ядра mihomo (бывший Clash Meta), все серверы в одном файле.
- **Ядро.** «Движок» внутри приложения, который реально соединяется с сервером: Xray, sing-box или mihomo. Приложение — это только оболочка вокруг ядра.
- **User-agent.** Подпись, которой приложение представляется при запросе подписки, например `v2rayNG/2.3.15`. По ней панель решает, какой формат отдать.
- **Правила ответа (Response Rules, SRR).** Список правил в настройках подписки панели. Каждое правило звучит так: «если user-agent такой-то, отдать такой-то формат, с таким-то шаблоном и такими-то заголовками». Панель проверяет правила сверху вниз, срабатывает первое подходящее.
- **Шаблон.** Заготовка файла настроек (для Xray JSON, sing-box или mihomo), куда панель вставляет наши серверы. Маршрутизация «Россия напрямую» пишется именно в шаблон.

## Коротко

1. **Почти все приложения из списка получают обычные ссылки (Xray base64).** Это Hiddify, Streisand, v2rayNG, v2rayN, NekoBox, Throne, Happ, v2RayTun. В ссылках xhttp сохраняется. Поймёт ли его приложение, зависит уже от ядра приложения.
2. **Приложения на mihomo** (FlClashX, Clash Verge Rev, Koala Clash, Prizrak-Box) получают mihomo YAML. **Все 8 наших серверов, включая xhttp, туда попадают:** Remnawave 3.4.5 умеет записывать xhttp для mihomo, а mihomo умеет xhttp начиная с версии v1.19.22 (апрель 2026).
3. **Приложения на sing-box** (официальные sing-box SFA/SFI/SFM и Karing) получают sing-box JSON. **Из него Remnawave молча выкидывает все xhttp-серверы.** Из наших 8 остаются 4: tcp+vision ×2, gRPC и ws через Cloudflare. Ошибки не будет: клиент просто увидит меньше серверов. Официальный sing-box (последняя стабильная 1.14.4 и тестовая 1.15.0-alpha.13) xhttp не умеет вообще.
4. **Формат можно задать любому приложению вручную.** Для этого нужно своё правило ответа по user-agent, поставленное выше стандартных: `responseType` выбирает формат, `subscriptionTemplate` выбирает шаблон.
5. **«Россия напрямую» для sing-box, mihomo и Xray JSON делается шаблонами**: для каждого формата свой шаблон с правилами, а правило ответа для приложения указывает на этот шаблон. Сейчас стандартный шаблон mihomo стоит в режиме `mode: global`, и в этом режиме правила не применяются. Его надо переводить в `mode: rule`.

## 1. Какой формат получает каждое приложение

Как панель выбирает формат по умолчанию, видно по стандартным правилам ответа, которые Remnawave создаёт при установке: https://github.com/remnawave/backend/blob/3.4.5/prisma/seed/default/response-rules.ts

| Порядок | Правило | user-agent (регулярное выражение, регистр не важен) | Формат |
|---|---|---|---|
| 1 | Browser Subscription | заголовок `accept` содержит `text/html` | страница в браузере |
| 2 | Mihomo Clients | `^(?:flclash\|rabbit\|flowvy\|murge\|mihomo\|prizrak-box\|koala-clash\|clash(?:-verge\|-nyanpasu\|x meta\|[-.]?meta))` | MIHOMO |
| 3 | Stash | `^stash` | STASH |
| 4 | Sing-box clients | `^sfa\|sfi\|sfm\|sft\|karing\|singbox` | SINGBOX |
| 5 | Clash Core Clients | `^clash` | CLASH (старый Clash, VLESS не поддерживает) |
| 6 | Fallback Base64 | всё остальное | XRAY_BASE64 |

Есть ещё одна дополнительная ступень. Если в настройках подписки включено «Serve JSON at Base Subscription» и запрос попал в base64, то приложения из отдельного списка получают Xray JSON вместо ссылок. В этом списке Streisand, Happ, INCY, V2Box, v2rayNG, v2rayN, v2plus и `io.github.saeeddev94.xray`.
Источники: https://github.com/remnawave/backend/blob/3.4.5/src/modules/subscription-template/constants/extended-clients.ts и https://github.com/remnawave/backend/blob/3.4.5/src/modules/subscription/subscription.service.ts (блок `serveJsonAtBaseSubscription`). **У нас эта настройка выключена** (`serveJsonAtBaseSubscription=false`, запись в `.scratch/tsproxy-bot/issues/37-podklyucheniya-i-maskirovka.md`).

Важно: стандартные правила создаются при установке панели. Какие правила стоят у нас сейчас, кроме двух наших (Happ и v2RayTun), панель не показывали. **[не проверено]** Косвенное подтверждение есть: в задаче 37 проверили, что Hiddify, Streisand и v2rayNG получают обычные ссылки (base64), а это совпадает с таблицей.

### Приложения по отдельности

| Приложение | user-agent (откуда взят) | Какое правило сработает | Формат у нас | Ядро и xhttp |
|---|---|---|---|---|
| **Happ** | `Happ/…` | наше правило «Happ — Россия напрямую» | base64 + заголовок `routing` | Xray, xhttp есть |
| **v2RayTun** | `v2raytun/…` | наше правило «v2RayTun — Россия напрямую» | base64 + заголовок `routing` | Xray, xhttp есть |
| **Hiddify** (5.5.5 от 2026-11-04) | `HiddifyNext/<версия> (<система>) like ClashMeta v2ray sing-box` ([исходник](https://github.com/hiddify/hiddify-app/blob/main/lib/core/model/app_info_entity.dart)) | никакое: регулярные выражения не совпадают, «sing-box» с дефисом не равно `singbox` | base64 | В примечаниях к 5.5.5 прямо написано «XHTTP» ([релиз](https://github.com/hiddify/hiddify-app/releases/tag/v5.5.5)). Каким ядром Hiddify обрабатывает xhttp — **[не проверено]** |
| **Streisand** | начинается со `Streisand` (по регулярному выражению Remnawave; код приложения закрыт) | никакое | base64 | Ядро и xhttp — **[не проверено]**, проверять в задачах 05/06 |
| **v2rayNG** (2.3.15) | `v2rayNG/<версия>` ([исходник HttpUtil.kt](https://github.com/2dust/v2rayNG/blob/master/V2rayNG/app/src/main/java/com/v2ray/ang/util/HttpUtil.kt)) | никакое | base64 | Xray-core v26.10.31 ([релиз](https://github.com/2dust/v2rayNG/releases/tag/2.3.15)), xhttp есть |
| **v2rayN** (7.25.6) | `v2rayN/<версия>`, если пользователь не задал свой ([исходник Utils.cs, строка ~930](https://github.com/2dust/v2rayN/blob/master/v2rayN/ServiceLib/Common/Utils.cs)) | никакое | base64 | Xray (и по выбору sing-box/mihomo). Для xhttp-серверов работает через Xray. Что будет, если пользователь сам переключит ядро на sing-box, — **[не проверено]** |
| **Throne** (1.4.1, бывший Nekoray) | `Throne/<версия>` ([исходник SettingsRepo.cpp](https://github.com/throneproj/Throne/blob/main/src/database/SettingsRepo.cpp)) | никакое | base64 | sing-box и встроенный Xray; в 1.4.1 «Fix XHTTP Extra field parsing» ([релиз](https://github.com/throneproj/Throne/releases/tag/1.4.1)), значит xhttp есть |
| **NekoBox (Android)** | **[не проверено]** | скорее всего никакое | base64 | sing-box. Последний релиз 1.4.2 вышел 2026-02-09 ([релизы](https://github.com/MatsuriDayo/NekoBoxForAndroid/releases)), это старше 3 месяцев, так что по нашим условиям приложение и так не подходит |
| **FlClashX** | `FlClash X/…` | Mihomo Clients | MIHOMO | mihomo. Последний стабильный релиз v0.4.2 от 2026-07-18, тестовые v0.5.0-pre.* вышли в октябре–ноябре ([релизы](https://github.com/pluralplay/FlClashX/releases)). Версия mihomo внутри — **[не проверено]**, нужна ≥ v1.19.22 |
| **Clash Verge Rev** (2.5.10) | `clash-verge/…` (по регулярному выражению Remnawave) | Mihomo Clients | MIHOMO | mihomo v1.19.33 (с 2.5.9, [релиз](https://github.com/clash-verge-rev/clash-verge-rev/releases/tag/v2.5.9)), xhttp есть |
| **Koala Clash** | `koala-clash/…` | Mihomo Clients | MIHOMO | mihomo, версия **[не проверено]** |
| **Prizrak-Box** | `prizrak-box/…` | Mihomo Clients | MIHOMO | mihomo, версия **[не проверено]** |
| **Karing** (1.2.26) | `karing…` (по регулярному выражению Remnawave) | Sing-box clients | **SINGBOX → xhttp выпадает** | Своя версия sing-box. В ней xhttp **есть**: коммит «v2ray xhttp/kcp» от 2025-12 и «update xhttp» от 2026-07 в ветках `karing_v1.13.13`…`karing_v1.14.4` ([репозиторий](https://github.com/KaringX/sing-box), [коммит](https://github.com/KaringX/sing-box/commit/28457f54)). Только Remnawave эти серверы ему не отдаёт |
| **sing-box SFA / SFI / SFM** | начинается с `SFA`/`SFI`/`SFM` | Sing-box clients | SINGBOX → xhttp выпадает | Официальный sing-box, xhttp нет |
| **Stash** (iOS) | `Stash/…` | Stash | STASH → xhttp выпадает | — |

Две особенности регулярных выражений Remnawave:
- В правиле sing-box знак `^` («начало строки») стоит только перед `sfa`. Остальные слова (`sfi`, `sfm`, `sft`, `karing`, `singbox`) ищутся **в любом месте** user-agent. Если какое-то приложение случайно содержит, например, «sft», оно получит sing-box. Среди наших кандидатов таких нет.
- Правило «Clash Core Clients» (`^clash`) отдаёт формат старого Clash. **В нём VLESS запрещён полностью**: `UNSUPPORTED_PROTOCOLS = ['hysteria', 'vless']` ([исходник](https://github.com/remnawave/backend/blob/3.4.5/src/modules/subscription-template/generators/clash.generator.service.ts)). Приложение, которое представится просто как `clash…` (а не `clash-verge`, `clash.meta` и т. п.), получит пустую подписку.

## 2. xhttp в sing-box и mihomo

### sing-box (официальный): xhttp нет

- В документации sing-box перечислены транспорты HTTP, WebSocket, QUIC, gRPC, HTTPUpgrade. xhttp среди них нет: https://sing-box.sagernet.org/configuration/shared/v2ray-transport/
- В исходниках последней стабильной **v1.14.4** (2026-10-23) и тестовой **v1.15.0-alpha.13** (2026-10-24) нет ни одного файла или коммита с xhttp/splithttp (проверено по репозиторию https://github.com/SagerNet/sing-box, папка `transport/`).

### mihomo: xhttp есть, с режимами stream-one и packet-up

- xhttp добавлен в mihomo 2026-03-27 (PR «feat: add xhttp transport support», https://github.com/MetaCubeX/mihomo/pull/2645). Первая версия с ним — **v1.19.22** (2026-04-01). Последняя сейчас — **v1.19.33** (2026-10-21). Позже добавлены stream-up и `download-settings` (PR #2655), `session-table`, исправления `extra.headers` (сентябрь 2026).
- Режимы `auto`, `stream-one`, `stream-up`, `packet-up` есть в коде клиента (`transport/xhttp/client.go`, v1.19.33) и в документации: https://wiki.metacubex.one/en/config/proxies/transport/ . xhttp работает только с VLESS. По умолчанию используется HTTP/2 (это совпадает с нашим `alpn h2` через Cloudflare).
- Reality вместе с xhttp поддерживается: в `adapter/outbound/vless.go` настройки Reality передаются в xhttp-клиент, в том числе для `download-settings` (https://github.com/MetaCubeX/mihomo/blob/v1.19.33/adapter/outbound/vless.go).

### Что делает Remnawave 3.4.5 с xhttp-серверами в каждом формате

| Формат | Что с xhttp | Источник |
|---|---|---|
| Xray base64 | остаётся (обычная ссылка `vless://…type=xhttp…`) | `xray.generator.service.ts` |
| Xray JSON | остаётся: `mode`, `host`, `path`, `extra` переносятся как есть | https://github.com/remnawave/backend/blob/3.4.5/src/modules/subscription-template/generators/xray-json.generator.service.ts |
| **sing-box** | **выкидывается молча**: `UNSUPPORTED_TRANSPORTS = new Set(['kcp', 'xhttp'])`, хост пропускается без ошибки | https://github.com/remnawave/backend/blob/3.4.5/src/modules/subscription-template/generators/singbox.generator.service.ts (строки 103 и 129) |
| **mihomo** | **остаётся**: пишется `network: xhttp` и `xhttp-opts` (path, host, mode, headers, поля `extra`, `xmux` → `reuse-settings`, `downloadSettings` → `download-settings`). Выкидывается только kcp | https://github.com/remnawave/backend/blob/3.4.5/src/modules/subscription-template/generators/mihomo.generator.service.ts (строки 86–110, 359–480). Код добавлен в апреле 2026, задолго до 3.4.5 |
| Stash | выкидывается (`if (isStash && host.transport === 'xhttp') continue`) | тот же файл, строка 159 |
| Clash (старый) | выкидывается весь VLESS | `clash.generator.service.ts`, строки 49–50 |

Кроме того, в sing-box выкидывается VLESS с включённым шифрованием VLESS (`encryption` ≠ `none`). У наших 8 хостов его нет.

**Что увидит клиент.** В sing-box-формате из наших 8 серверов останутся 4: «Германия — Запасной 1» (tcp+vision), «Запасной 2» (gRPC), «Обход 1» (ws через Cloudflare) и «Германия 2 — Основной» (tcp+vision). Пропадут «Основной» (xhttp 443), «Запасной 3» (packet-up), «Обход 2» (xhttp через Cloudflare) и «Германия 2 — Запасной» (xhttp 8444). Ошибки импорта не будет. Если бы xhttp-серверами были все, список в приложении оказался бы пустым **[не проверено, что приложение покажет в этом случае]**.

Для mihomo-формата есть оговорка **[не проверено]**. Наши xhttp-хосты могут содержать поля `extra`, которых нет в таблице соответствий Remnawave (`XHTTP_FIELD_MAP`). Такие поля просто не попадут в mihomo. Работают ли наши 4 xhttp-сервера в mihomo на деле, нужно проверить на стенде (задачи 03/05).

## 3. Как принудительно отдать приложению нужный формат

Для этого в правила ответа подписки добавляется своё правило **выше** стандартных: срабатывает первое подходящее. Поля правила описаны в документации https://docs.rw/learn-en/routing-rules и в схеме https://github.com/remnawave/backend/blob/3.4.5/libs/contract/models/response-rules/response-rule-modifications.schema.ts :

- `conditions`: проверка заголовка, например `user-agent`, оператор `REGEX`, значение `^karing`, `caseSensitive: false`;
- `responseType`: формат, один из `XRAY_BASE64`, `XRAY_JSON`, `SINGBOX`, `MIHOMO`, `STASH`, `CLASH`;
- `responseModifications.subscriptionTemplate`: имя своего шаблона (например, «ru-direct»). Если шаблона с таким именем нет, берётся стандартный;
- `responseModifications.headers`: свои заголовки (так у нас уже сделаны Happ и v2RayTun);
- `ignoreHostXrayJsonTemplate`: не брать шаблон Xray JSON, привязанный к хосту, а взять шаблон из правила;
- `ignoreServeJsonAtBaseSubscription`, `excludeHostsByTags` (убрать хосты с указанными тегами), `disableHwidCheck`.

Правила ответа главнее настроек External Squads.

Что из этого следует для нас (решает владелец):
- **Karing:** правило `^karing` → `XRAY_BASE64` (или `MIHOMO`) вместо sing-box, тогда xhttp-серверы до него дойдут. Разбирает ли Karing xhttp из ссылки `vless://` (или `xhttp-opts` из mihomo), **[не проверено]**: нужен стенд.
- **Официальный sing-box (SFA/SFI/SFM):** переключать формат бессмысленно, ядро xhttp не умеет. Такое приложение получит 4 сервера из 8, и это надо честно написать в инструкции (или не брать его в список).
- **v2rayNG / v2rayN / Streisand:** чтобы дать им «Россию напрямую», нужно правило по user-agent → `XRAY_JSON` + `subscriptionTemplate`. Включать глобальную настройку «Serve JSON at Base Subscription» не стоит: тогда JSON получит и Happ, а он сейчас настроен через заголовок (см. ниже).

## 4. «Россия напрямую» через шаблоны

Общий принцип: делаем отдельный шаблон каждого нужного формата с правилами «российское — напрямую, остальное — через VPN» и правило ответа для приложения с `subscriptionTemplate: <имя шаблона>`. Шаблонов одного типа может быть несколько, начиная с Remnawave 2.2.0 (https://docs.rw/learn-en/templates). Стандартные шаблоны лежат здесь: https://github.com/remnawave/backend/blob/3.4.5/src/modules/subscription-template/constants/default-templates.ts

Что напрямую: как в профиле Happ — домены `.ru`, `.su`, `.рф` (`xn--p1ai`), список `category-ru` и российские IP-адреса (`geoip ru`), плюс локальные сети. Файлы со списками доступны (проверено, отвечают 200):
- для sing-box: https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-ru.srs и https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-category-ru.srs
- для mihomo: https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geoip/ru.mrs и https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/category-ru.mrs
- для Xray (сам список): https://github.com/v2fly/domain-list-community/blob/master/data/category-ru

Риск **[не проверено]**: эти файлы приложение скачивает с GitHub. Если GitHub из России открывается плохо, правила могут не загрузиться при первом запуске. Поэтому скачивать их лучше через VPN (поле `proxy` у mihomo, `download_detour`/`http_client` у sing-box).

### mihomo (FlClashX, Clash Verge Rev, Koala Clash, Prizrak-Box)

В стандартном шаблоне стоит `mode: global`, а по документации mihomo `global` означает «всё через выбранный прокси» (https://wiki.metacubex.one/en/config/general/). Значит, правила работают только при `mode: rule`. Строки `proxies: # LEAVE THIS LINE!` трогать нельзя: в эти места Remnawave вставляет серверы. Набросок шаблона (только изменённые части):

```yaml
mode: rule

rule-providers:
  ru-domains:
    type: http
    behavior: domain
    format: mrs
    url: https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/category-ru.mrs
    interval: 86400
    proxy: → Remnawave
  ru-ip:
    type: http
    behavior: ipcidr
    format: mrs
    url: https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geoip/ru.mrs
    interval: 86400
    proxy: → Remnawave

rules:
  - DOMAIN-SUFFIX,ru,DIRECT
  - DOMAIN-SUFFIX,su,DIRECT
  - DOMAIN-SUFFIX,xn--p1ai,DIRECT
  - RULE-SET,ru-domains,DIRECT
  - RULE-SET,ru-ip,DIRECT,no-resolve
  - MATCH,→ Remnawave
```

Формат `mrs` работает только с `behavior: domain` и `ipcidr` (https://wiki.metacubex.one/en/config/rule-providers/). Как Remnawave расширяет шаблон mihomo своими ключами, описано здесь: https://docs.rw/guides/templates/mihomo/ . Сам шаблон **[не проверено]** на стенде. Отдельный вопрос **[не проверено]**: не перебивает ли приложение режим `rule`/`global` своей настройкой (в Clash Verge Rev режим переключается в интерфейсе).

### sing-box (SFA/SFI/SFM, Karing, если оставим ему sing-box)

С sing-box 1.12 старые базы geoip/geosite удалены, нужны rule-set (https://sing-box.sagernet.org/deprecated/, https://sing-box.sagernet.org/configuration/rule-set/). Набросок раздела `route` (выход `direct` и группа `→ Remnawave` в стандартном шаблоне уже есть):

```json
"route": {
  "rules": [
    { "action": "sniff" },
    { "action": "hijack-dns", "protocol": "dns" },
    { "ip_is_private": true, "outbound": "direct" },
    { "domain_suffix": ["ru", "su", "xn--p1ai"], "outbound": "direct" },
    { "rule_set": ["geosite-category-ru", "geoip-ru"], "outbound": "direct" }
  ],
  "rule_set": [
    { "tag": "geoip-ru", "type": "remote", "format": "binary",
      "url": "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-ru.srs",
      "download_detour": "→ Remnawave" },
    { "tag": "geosite-category-ru", "type": "remote", "format": "binary",
      "url": "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-category-ru.srs",
      "download_detour": "→ Remnawave" }
  ],
  "final": "→ Remnawave",
  "auto_detect_interface": true,
  "default_domain_resolver": "local"
}
```

Здесь два места **[не проверено]**:
- В sing-box 1.14 поле `download_detour` объявлено устаревшим (его заменяет `http_client`, удалят в 1.16), но пока работает.
- Стандартный шаблон использует «поддельные» DNS-адреса (fakeip). С ними правило по российским IP может не сработать без шага `resolve`. Нужна проверка на стенде.

### Xray JSON (v2rayNG, v2rayN, Streisand, если дать им JSON)

Remnawave делает отдельный полный конфиг на каждый сервер: берёт шаблон, ставит наш сервер первым выходом с меткой `proxy`, а `routing` берёт из шаблона как есть (`xray-json.generator.service.ts`, строки 204–256). В шаблон добавляется правило:

```json
{ "type": "field", "outboundTag": "direct",
  "domain": ["domain:ru", "domain:su", "domain:xn--p1ai", "geosite:category-ru"],
  "ip": ["geoip:ru", "geoip:private"] }
```

Учтите две вещи:
- **У хостов «Германии» уже привязаны шаблоны Xray JSON** `ru-direct` и `ru-direct-2` (задача 37). Шаблон хоста главнее, если в правиле нет `ignoreHostXrayJsonTemplate: true`. При этом в `ru-direct` есть правило на несуществующий `balancerTag: AX-BALANCER`, и до выдачи JSON клиентам его нужно исправить или убрать.
- `geosite:category-ru` и `geoip:ru` работают, только если в файлах geo внутри приложения есть такие категории. Для v2rayNG/v2rayN/Streisand это **[не проверено]**. Надёжнее оставить и явные `domain:ru` и т. п.

Для Happ и v2RayTun ничего не меняем: у них «Россия напрямую» уже приходит заголовком `routing`.

## Итог для выбора приложений (задача 04)

| Сочетание | xhttp у клиента |
|---|---|
| Xray-приложения (Happ, v2RayTun, v2rayNG, v2rayN) через base64 или Xray JSON | все 8 серверов |
| Hiddify 5.5.5, Throne 1.4.1 через base64 | заявлено, что xhttp есть, проверить на стенде |
| mihomo-приложения (Clash Verge Rev 2.5.10 с mihomo 1.19.33; FlClashX, Koala Clash, Prizrak-Box, если внутри mihomo ≥ 1.19.22) | все 8 серверов, проверить поля `extra` на стенде |
| **Karing** (sing-box формат по умолчанию) | **теряет 4 xhttp-сервера** из-за Remnawave, хотя ядро xhttp умеет. Лечится правилом ответа → base64/mihomo **[не проверено]** |
| **Официальный sing-box SFA/SFI/SFM** | **теряет 4 xhttp-сервера**, не лечится: ядро не умеет |
| **Stash** | **теряет xhttp** (Remnawave выкидывает) |
| Приложения, попавшие под `^clash` (старый Clash) | теряют **все** серверы (VLESS запрещён) |
| v2rayN с ядром sing-box, переключённым пользователем | **[не проверено]** |
