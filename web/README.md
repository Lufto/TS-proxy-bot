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

## Языки и адреса

Язык по умолчанию русский, без приставки: `/`, `/help/`, `/terms/`, `/privacy/`. Английский — те же адреса с `/en`. Адрес без слэша (`/terms`) переадресуется на адрес со слэшем. На `/terms` и `/privacy` ссылается бот, эти адреса не меняем.

- Тексты страниц: `apiStatic/<язык>/constructor/page/<адрес>.ts`. Главная лежит в `index.ts`, страница `/help/ios/` будет в `help/ios.ts`.
- Шапка и подвал: `apiStatic/<язык>/common.ts`.
- Короткие подписи интерфейса (например, «страница не найдена»): `src/data/<язык>/*.json` (next-intl).

Страница собирается из блоков. Блок — это виджет в `src/widgets/`. Его схема (valibot) зарегистрирована в `src/app-layer/config/block-schema.tsx`, компонент — в `block-component.tsx`. Сейчас есть только блок-заглушка `stub`.

**UI-кит** — `src/widgets/kit/`: три варианта стиля (`?v=a|b|c`) по адресу `/kit`, закрыт от поисковиков. Пока выбираем стиль, кит стоит и на корне (`app/[locale]/page.tsx`); когда будет главная, корень вернётся к конструктору.

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
