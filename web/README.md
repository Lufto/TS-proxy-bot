# Сайт ts-proxy.com

Новый сайт TS Proxy на Next 16, React 19, TypeScript и Tailwind 4. Собран из шаблона агентства `nextjs-ts-tailwind` (`create-app`). План и задачи лежат в `../.scratch/tsproxy-site/`.

Бэкенда у сайта нет. Данные страниц лежат в `apiStatic/`, их читает мок-клиент `@idem.agency/fetch`: при сборке `idem_mock` генерирует `src/generated.ts`.

## Как поставить и запустить

```sh
cp .env.example .env
npm ci
npm run dev        # разработка: http://localhost:3000
npm run build      # сборка
npm start          # запуск собранного сайта
npm run check      # проверка запущенного сайта по HTTP (другой адрес — CHECK_URL=...)
npm run lint       # eslint
npm run test:unit  # юнит-тесты (jest)
```

Токен GitLab агентства не нужен: закрытые пакеты лежат архивами в `vendor/`.

## Общий конфиг

Тарифы, имя бота, почта поддержки, адрес сайта, адрес состояния бота и ссылки на приложения лежат в `../config/` (`tariffs.yaml`, `site.yaml`) — общие с ботом. `scripts/generate-config.mjs` перед `dev`, `build` и `test` пишет из них `src/generated-config.ts` (в git не попадает). В текстах `apiStatic` адрес `bot` — ссылка в бота, `support` — в поддержку бота, `{email}` — почта; руками они нигде не вписаны. Скидку сайт считает той же формулой, что бот (`src/shared/lib/tariffs`).

## Языки и адреса

Язык по умолчанию русский, без приставки: `/`, `/help/`, `/terms/`, `/privacy/`. Английский — те же адреса с `/en`. Адрес без слэша (`/terms`) переадресуется на адрес со слэшем. На `/terms` и `/privacy` ссылается бот, эти адреса не меняем.

- Тексты страниц: `apiStatic/<язык>/constructor/page/<адрес>.ts`. Главная лежит в `index.ts`, страница `/help/ios/` будет в `help/ios.ts`.
- Шапка и подвал: `apiStatic/<язык>/common.ts`.
- Короткие подписи интерфейса (например, «страница не найдена»): `src/data/<язык>/*.json` (next-intl).

Страница собирается из блоков — виджетов в `src/widgets/`: `hero`, `features`, `steps`, `tariffs`, `devices`, `text-list`, `cta`, `page-head`, `document`, `app-steps`. Схема блока (valibot) зарегистрирована в `src/app-layer/config/block-schema.tsx`, компонент — в `block-component.tsx`. Новая страница — файл в `apiStatic/<язык>/constructor/page/` и строка в `src/shared/config/pages.ts` (карта сайта) и `scripts/check-site.mjs`.

Плашка о неполадках — `src/widgets/status-banner`: раз в минуту спрашивает `status_url` бота, тексты — в `src/data/<язык>/common.json`. Картинка превью ссылок — `public/meta/og.png`, исходник и пересборка — `scripts/og/`.

**UI-кит «Манифест»** — `/kit`, закрыт от поисковиков; пока нет главной, стоит и на корне (`app/[locale]/page.tsx`). Токены — `src/assets/styles/theme.css` (цвета `bg`, `surface`, `line`, `cream`, `muted`, `accent`, `on-accent`; шрифты `font-display`, `font-body`, `font-mono`) и `typography.css` (классы `t-display`, `t-h1`…`t-label`). Компоненты — `src/shared/ui/` (`button`, `link`, `logo`, `burger`, `reveal`, `lang-switch`), шапка и подвал — `src/widgets/header`, `src/widgets/footer`. Шрифты лежат в `src/assets/fonts` и подключены в `fonts.css`, без `next/font/google`. Черновики трёх вариантов и знаков удалены после выбора; они есть в истории git (коммит `588611b`).

Папка `pages/` в корне пустая, но удалять её нельзя. Без неё Next примет `src/pages/` (реализации страниц для `app/`) за старый Pages Router, и сборка упадёт.

## Что убрано из шаблона и почему

| Что | Почему |
|---|---|
| `react-google-recaptcha-v3`, `@mona-health/react-input-mask` | на сайте нет форм |
| `src/shared/lib/yandex-metrika.tsx`, ключ Яндекс.Карт | счётчиков и карт нет: владелец отказался от аналитики |
| `generate-openapi.mjs` и его зависимости (`@valibot/to-json-schema`, `fast-glob`, `tsconfig-paths`, `tsx`, `ts-node`, `cross-env`) | нет бэкенда, которому нужна спецификация API |
| `express`, `body-parser`, `i18next`, `isomorphic-dompurify`, `zustand`, `swiper`, `@floating-ui/react` | в шаблоне не используются; понадобятся — вернём |
| `husky` | git-хуки общие для репозитория, в корневом `.pre-commit-config.yaml` (там же eslint сайта) |
| виджет `example`, страница `another-page` | примеры шаблона |

Логгер `winston` остался: он пишет отчёты о данных, не прошедших проверку схемой, и включается переменной `VALIDATION_LOG_ENABLED`. `js-yaml` остался для чтения общего конфига `../config/`.

## Как обновить закрытые пакеты

Нужен доступ к реестру агентства (см. README `create-app`):

```sh
cd vendor
npm pack @idem.agency/fetch@<версия> @idem.agency/popups-engine@<версия>
```

Потом поменять имена архивов в `package.json`, выполнить `npm install` и удалить старые архивы.
