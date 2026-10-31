# Сайт ts-proxy.com

Статический сайт TS Proxy (задача 25): чистый HTML и CSS, без сборки и библиотек. Единственный скрипт — плашка «Возможны неполадки».

## Структура

| Адрес | Файл | Что там |
|---|---|---|
| `/` | `index.html` | Главная: что это, кнопка «Открыть бота» |
| `/tariffs/` | `tariffs/index.html` | Цены и что входит (цены — как в `content/tariffs.yaml`) |
| `/help/` | `help/index.html` | Инструкции: Happ на iOS, Android, Windows, macOS; v2RayTun — запасное |
| `/support/` | `support/index.html` | Контакты поддержки |
| `/terms/` | `terms/index.html` | Пользовательское соглашение с условиями возврата |
| `/privacy/` | `privacy/index.html` | Политика конфиденциальности |
| — | `404.html` | Страница «не найдено» |
| — | `assets/style.css` | Общий стиль, светлая и тёмная тема по настройке системы |
| — | `assets/status.js` | Плашка: раз в минуту спрашивает `/health` бота |
| — | `assets/logo.svg` | Логотип и значок вкладки |

Шапка и подвал повторяются в каждой странице: меняете меню или подвал — меняйте во всех `index.html`. Меняете текст соглашения или политики — обновите дату редакции вверху документа. Меняете цены в боте — поменяйте и на `/tariffs/` (и «от 120 ₽» на главной).

## Что заменить перед запуском

Все заглушки ищутся по `TODO(владелец)`: `grep -rn "TODO" site`.

1. **Имя бота.** Во всех файлах стоит `https://t.me/TODO_BOT_USERNAME`. Заменить одной командой (вместо `имя_бота` — имя без @):
   ```sh
   grep -rl TODO_BOT_USERNAME site | xargs sed -i '' 's/TODO_BOT_USERNAME/имя_бота/g'
   ```
2. **Адрес проверки бота** — константа `HEALTH_URL` в `assets/status.js` (сейчас `https://bot.ts-proxy.com/health`). Пока бот по этому адресу не отвечает, плашка будет видна всегда.
3. **Почта поддержки.** Сейчас `tsarewalex2006@gmail.com`; каждое место помечено комментарием `<!-- почта поддержки, см. README -->`. Когда заработает `support@ts-proxy.com` (задача 34):
   ```sh
   grep -rl tsarewalex2006@gmail.com site | xargs sed -i '' 's/tsarewalex2006@gmail.com/support@ts-proxy.com/g'
   ```
   и обновить дату редакции в `terms/` и `privacy/`.
4. **Скриншоты** для `/help/` — по три на платформу (iOS, Android, Windows, macOS), подписи-заглушки на странице говорят, что должно быть на каждом. Положить картинки в `help/img/` и заменить каждый блок `<figure class="shot">…</figure>` на
   ```html
   <img src="/help/img/ios-1.png" alt="Happ в App Store" width="390" height="844" loading="lazy">
   ```
   Ширина и высота — настоящие размеры картинки (чтобы страница не прыгала при загрузке). Перед публикацией сжать картинки и убедиться, что на них не видно ссылки подписки и личных данных.
5. **Ссылки на приложения** Happ и v2RayTun (App Store, Google Play, Windows) — добавить в шаги на `/help/`.

## Как выложить (Cloudflare Pages, без сборки)

1. Cloudflare → **Workers & Pages** → **Create** → **Pages** → подключить репозиторий (или **Upload assets** и перетащить папку `site/`).
2. Настройки сборки: Framework preset — **None**, Build command — пусто, Build output directory — **`site`**.
3. После первой выкладки: **Custom domains** → добавить `ts-proxy.com` (и при желании `www.ts-proxy.com`).
4. Проверить: открываются `/`, `/tariffs/`, `/help/`, `/support/`, `/terms/`, `/privacy/`; адрес без слэша (`/terms`) тоже открывается — бот ссылается именно на `https://ts-proxy.com/terms` и `https://ts-proxy.com/privacy`.

Почему Pages, а не сервер бота: если сервер упадёт, сайт останется доступен и покажет плашку. В `docs/server-setup.md` пока записано «сайт раздаётся с этого сервера» — решить, какой вариант оставить.

## Проверить локально

```sh
python3 -m http.server -d site 8765
```
и открыть `http://localhost:8765/`. Плашка локально будет видна, пока бот не отвечает по адресу из `HEALTH_URL`.
