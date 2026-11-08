# Запасная страница, когда сервер или туннель упал

Дата: 2026-11-08. Отвечает на задачу `.scratch/tsproxy-site/issues/01-zapasnaya-stranitsa.md`.
Метод: чтение документации Cloudflare (developers.cloudflare.com). Где документация молчит, это сказано прямо. Сервер не трогали, ничего не проверяли вживую.

Обозначения: **[факт]** прямо написано в источнике; **[вывод]** моё заключение; **[нет в докс]** документация об этом молчит.

## Коротко

**Рекомендация: маленький Worker на маршруте `ts-proxy.com/*`, в который вшита запасная страница (Workers Static Assets).** Worker пропускает каждый запрос на наш сервер через туннель как обычно. Если сервер не ответил, ответил ошибкой 5xx (в том числе 530/1033, когда туннель лёг) или не уложился в несколько секунд, Worker отдаёт запасную страницу. Это работает на бесплатном тарифе и стоит 0 ₽. Запасная копия лежит у Cloudflare, а не на нашем сервере, поэтому переживает его падение.

Остальные варианты не подходят:
- **Собственные страницы ошибок Cloudflare (Custom Errors / Error Pages)** на бесплатном тарифе недоступны, нужен платный Pro или выше.
- **Always Online** бесплатен, но показывает старую копию сайта из Internet Archive (обновляется раз в 30 дней, только популярные страницы). Сообщения «мы чиним» в ней не будет, а про туннели и ошибку 530/1033 в документации ничего нет.

## Словарик

- **Туннель (Cloudflare Tunnel, `cloudflared`)**: программа на нашем сервере, которая сама держит связь с Cloudflare. Посетитель приходит в Cloudflare, Cloudflare передаёт запрос по туннелю на сервер. Порт 443 на сервере для этого не нужен.
- **Worker**: маленькая программа на JavaScript, которая работает на серверах Cloudflare *перед* нашим сайтом. Видит каждый запрос и может решить, что ответить.
- **Маршрут (route)**: правило «для адресов `ts-proxy.com/*` запускать такой-то Worker».
- **Static Assets**: файлы (HTML, картинки), которые загружаются в Cloudflare вместе с Worker и раздаются оттуда.
- **KV**: простое хранилище «ключ — значение» у Cloudflare.
- **Pages**: хостинг статических сайтов у Cloudflare. Для новых проектов Cloudflare сейчас советует Workers со Static Assets (есть гайд по переезду с Pages на Workers).

## Какие ошибки увидит посетитель без защиты

| Что сломалось | Что отдаёт Cloudflare | Источник |
|---|---|---|
| Сервер выключен или `cloudflared` остановлен, туннель не подключён | **1033** («туннель не подключён к сети Cloudflare») | [факт] [Error 1033](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-1xxx-errors/error-1033/), [Tunnel common errors](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/troubleshoot-tunnels/common-errors) |
| Туннель жив, но сам сайт (Next) не отвечает | **502 Bad Gateway** («проблема между `cloudflared` и локальной службой») | [факт] [Tunnel common errors](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/troubleshoot-tunnels/common-errors) |
| HTTP-статус, с которым приходит 1033 | **530**: «тело ответа 530 содержит код ошибки 1XXX» | [факт] [Error 530](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-530/). Что 1033 приходит именно как 530, на странице 1033 прямо не написано **[нет в докс]**. Это подтверждают сообщения в сообществе: Worker видит статус 530, браузер видит страницу 1033 ([пример](https://www.answeroverflow.com/m/1075547876073148517)) |

## Вариант 1. Worker с запасной страницей (рекомендую)

### Как работает

1. В Cloudflare добавляем маршрут `ts-proxy.com/*` (и `www.ts-proxy.com/*`, если нужен) на Worker.
2. Worker делает `fetch(request)`. По документации такой запрос уходит «на ваш сервер приложения, как указано в настройках DNS», то есть в наш туннель. **[факт]** [Routes](https://developers.cloudflare.com/workers/configuration/routing/routes/)
3. Если `fetch` выбросил исключение, вышел по таймауту или вернул статус 5xx (502, 530 и т. п.), Worker отдаёт запасную страницу из Static Assets через `env.ASSETS.fetch(...)`. **[факт]** о `env.ASSETS.fetch` и о том, что ему можно передать свой запрос, чтобы взять другой файл: [Static Assets: Worker script](https://developers.cloudflare.com/workers/static-assets/routing/worker-script/)
4. Иначе Worker просто возвращает ответ сервера без изменений.

Набросок (не финальный код):

```js
export default {
  async fetch(request, env) {
    try {
      const res = await fetch(request, { signal: AbortSignal.timeout(8000) });
      if (res.status < 500) return res;
    } catch (e) { /* сервер или туннель недоступен, таймаут */ }
    const wantsHtml = request.method === "GET" && (request.headers.get("Accept") || "").includes("text/html");
    if (!wantsHtml) return new Response("Service unavailable", { status: 503 });
    const page = await env.ASSETS.fetch(new URL("/__fallback/index.html", request.url));
    return new Response(page.body, {
      status: 503,
      headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "Retry-After": "300" },
    });
  },
};
```

Почему так:
- **Статус 503 и `Retry-After`**: поисковики поймут, что сайт временно недоступен, и не заменят в выдаче наши страницы запасной. **[вывод]**
- **`no-store`**: чтобы Cloudflare и браузеры не закешировали запасную страницу, когда сервер уже поднялся. **[вывод]**
- **Запасная страница только для HTML-запросов**: картинкам и скриптам незачем получать HTML. **[вывод]**
- **Таймаут через `AbortSignal`**: `AbortController` и `AbortSignal` в Workers есть **[факт]** ([Web standards](https://developers.cloudflare.com/workers/runtime-apis/web-standards/)). Про `AbortSignal.timeout` там не сказано **[нет в докс]**; если не заработает, тот же таймаут делается через `AbortController` и `setTimeout`. Без таймаута посетитель, когда сервер «завис», ждал бы ответа очень долго.

### Ловушка с порядком «сначала файлы, потом Worker»

По умолчанию Cloudflare сначала ищет совпадающий файл в Static Assets и только если файла нет, запускает Worker. **[факт]** [Static Assets: Worker script](https://developers.cloudflare.com/workers/static-assets/routing/worker-script/). Если положить запасную страницу как `index.html` в корень, Cloudflare будет отдавать её на `/` **всегда**, даже когда сервер жив. Поэтому запасную страницу кладём в отдельную папку с необычным именем, например `/__fallback/index.html`, и ничего не включаем в `run_worker_first`. **[вывод]**

### Лимиты бесплатного тарифа

| Что | Лимит Free | Хватит ли нам |
|---|---|---|
| Запросов к Worker в сутки | 100 000, сброс в полночь UTC | [факт] [Limits](https://developers.cloudflare.com/workers/platform/limits/). Каждый запрос к сайту (страница, JS, CSS, картинка) запускает Worker. Одна страница Next это примерно 10–30 запросов, итого порядка 3–10 тысяч просмотров в сутки. Для начала с запасом. **[вывод]** |
| Что будет при превышении | Выбирается в настройках маршрута: **fail open** (Worker пропускается, сайт работает как без него) или fail closed (ошибка 1027) | [факт] [Limits](https://developers.cloudflare.com/workers/platform/limits/). Ставим **fail open**: при перерасходе пропадёт только запасная страница, сайт продолжит работать. |
| Время процессора | 10 мс на запрос | [факт] [Limits](https://developers.cloudflare.com/workers/platform/limits/), [Pricing](https://developers.cloudflare.com/workers/platform/pricing/). Наш Worker только пересылает запрос, это доли миллисекунды. Платы за длительность на Free нет **[факт]** [Pricing](https://developers.cloudflare.com/workers/platform/pricing/), так что ожидание ответа сервера в 10 мс не входит **[вывод]**. |
| Вложенных запросов (subrequests) | 50 на вызов | [факт] [Limits](https://developers.cloudflare.com/workers/platform/limits/). У нас 1–2. |
| Файлы Static Assets | до 20 000 файлов, до 25 МиБ каждый | [факт] [Limits](https://developers.cloudflare.com/workers/platform/limits/). У нас 1–3 файла. |
| Запросы к самим Static Assets | «бесплатно и без ограничений», в 100 000 не входят | [факт] [Pricing](https://developers.cloudflare.com/workers/platform/pricing/), [Billing and limitations](https://developers.cloudflare.com/workers/static-assets/billing-and-limitations/) |

**Как сэкономить лимит, если сайт вырастет.** Можно добавить маршрут **без Worker** для `ts-proxy.com/_next/*`. Документация разрешает маршруты без Worker: такой маршрут «отменяет менее конкретные шаблоны», и выигрывает самый конкретный. **[факт]** [Routes](https://developers.cloudflare.com/workers/configuration/routing/routes/). Тогда скрипты и стили Next идут мимо Worker, а Worker считает только страницы. Запасной странице это не мешает, если она самодостаточна (стили внутри файла, логотип вшит SVG). **[вывод]** Если трафика станет много, есть платный тариф Workers: от $5 в месяц **[факт]** [Pricing](https://developers.cloudflare.com/workers/platform/pricing/).

### Условия

- Для маршрута нужна DNS-запись хоста, **проксируемая Cloudflare (оранжевое облако)**. **[факт]** [Routes](https://developers.cloudflare.com/workers/configuration/routing/routes/). У туннеля CNAME на `<UUID>.cfargotunnel.com` и так проксируется. **[вывод]**
- Документация туннеля предупреждает: если маршруты Worker пересекаются с хостом туннеля и Worker «неправильно обрабатывает трафик», будут ошибки. Решение там же: Worker должен пересылать остальные запросы на сервер. **[факт]** [Tunnel common errors](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/troubleshoot-tunnels/common-errors). Наш набросок так и делает.
- Что Worker на маршруте получает от `fetch` при упавшем туннеле именно ответ 530, а не исключение, официально не описано **[нет в докс]**. Поэтому ловим оба случая: исключение и статус ≥ 500. Перед запуском проверить на тестовом поддомене: остановить `cloudflared` только для тестового хоста или направить тестовый хост на несуществующий туннель. Это работа на сервере, только после «ок» владельца.

### Как обновлять запасную копию при выкатке

- Запасная страница это один самодостаточный HTML-файл в репозитории, например `web/fallback/__fallback/index.html`, рядом с кодом Worker и его `wrangler.jsonc`. Тексты: «Сайт временно не работает, мы чиним. Поддержка: бот @…, support@ts-proxy.com», на русском и английском на одной странице. **[вывод]**
- Выкладка: `npx wrangler deploy` из папки Worker. Нужен API-токен Cloudflare с правом на Workers. **[вывод]**
- Обновлять при каждой выкатке сайта **не нужно**: страница меняется, только когда меняются контакты или бренд. Достаточно выкладывать Worker, когда в его папке что-то изменилось: вручную с Mac владельца или через GitHub Actions по изменению в `web/fallback/`. **[вывод]**
- Выкладывать Worker **с сервера не надо**: запасная страница нужна именно когда сервер лежит, поэтому её выкладка от сервера не должна зависеть. **[вывод]**
- Если хочется, чтобы запасная страница выглядела как сайт: Next может собрать её статически, и в скрипте выкатки этот HTML копируется в папку Worker. Для одной простой страницы проще и надёжнее написать её руками. **[вывод]**

### Стоимость

0 ₽ на бесплатном тарифе Workers. **[факт]** о том, что Free идёт по умолчанию, а минимум $5 в месяц только на Paid: [Pricing](https://developers.cloudflare.com/workers/platform/pricing/).

## Вариант 1б. Тот же Worker, но запасная копия в KV или на Pages

Работает, но сложнее без пользы:
- **KV**: на Free 100 000 чтений и 1 000 записей в сутки, 1 ГБ. **[факт]** [KV limits](https://developers.cloudflare.com/kv/platform/limits/). Чтение нужно только при аварии, так что лимита хватит. Но копию надо загружать в KV отдельной командой, и это ещё одна вещь, которая может разойтись с кодом. **[вывод]**
- **Pages**: запасная страница как отдельный сайт `*.pages.dev`, Worker при аварии запрашивает её оттуда. Это два проекта вместо одного и лишний запрос в момент аварии. Cloudflare сам ведёт с Pages на Workers со Static Assets ([гайд по переезду](https://developers.cloudflare.com/workers/static-assets/migration-guides/migrate-from-pages/)). **[вывод]**

## Вариант 2. Собственные страницы ошибок Cloudflare (Custom Errors, Error Pages)

- Custom Error Rules и Custom Error Assets: на Free **0** правил, на Pro 25, на Business 50. «Custom Errors доступны на всех платных тарифах». **[факт]** [Custom Errors](https://developers.cloudflare.com/rules/custom-errors/)
- Error Pages (свои страницы для «ошибок 500-го класса» и «ошибок 1000-го класса»): Pro, Business, Enterprise, на Free нет. **[факт]** [Custom Errors](https://developers.cloudflare.com/rules/custom-errors/), [Error Pages](https://developers.cloudflare.com/rules/custom-errors/edit-error-pages/)
- Входит ли 1033/530 в «ошибки 1000-го класса», документация не перечисляет. **[нет в докс]**
- **Итог:** на бесплатном тарифе недоступно. Платить за Pro только ради этого нет смысла, Worker делает то же бесплатно.

## Вариант 3. Always Online

- Есть на всех тарифах, на Free обход сайта раз в **30 дней**. **[факт]** [Always Online](https://developers.cloudflare.com/cache/how-to/always-online/)
- Копии хранит **Internet Archive (Wayback Machine)**. Cloudflare передаёт ему адрес сайта и популярные страницы; сохраняются не все страницы. Если страницы нет в архиве, посетитель видит обычную ошибку. **[факт]** там же
- Включается, «только когда Cloudflare вообще не может соединиться с сервером», а на ответ сервера 5xx «не вмешивается». **[факт]** [Always Online troubleshooting](https://developers.cloudflare.com/cache/troubleshooting/always-online/). Документация называет коды 520–527; про туннель, 530 и 1033 ничего нет. **[нет в докс]** Наш случай «туннель жив, Next упал» (502 от `cloudflared`) Cloudflare считает ответом сервера, и Always Online здесь, скорее всего, не сработает. **[вывод]**
- Покажет старую копию сайта до месяца давности, а не сообщение «мы чиним, пишите в поддержку». Наш сайт к тому же уйдёт в публичный архив. **[вывод]**
- **Итог:** не годится как запасная страница. Включать вместе с Worker незачем: при аварии Worker ответит раньше. **[вывод]**

## Решение

| Вариант | Free | Работает с туннелем (1033/530) | Свой текст «мы чиним» | Цена |
|---|---|---|---|---|
| **Worker + Static Assets** | да | да, ловим ошибку сами | да | 0 |
| Worker + KV / Pages | да | да | да | 0, но сложнее |
| Custom Errors / Error Pages | **нет** | не описано | да | только Pro и выше |
| Always Online | да | не описано, вряд ли | нет, старая копия | 0 |

Делаем **Worker на маршруте `ts-proxy.com/*` с запасной страницей в Static Assets**, режим маршрута **fail open**, таймаут к серверу около 8 секунд, ответ 503 + `no-store`. Запасная страница: один самодостаточный HTML-файл в репозитории; выкладывается `wrangler deploy` с Mac или из GitHub Actions, только когда она меняется.

Открытые вопросы для следующих задач:
- Точный юзернейм бота поддержки и тексты страницы (русский и английский).
- Кто выкладывает Worker: вручную или через GitHub Actions. Для Actions нужен токен Cloudflare в секретах репозитория.
- Проверка на тестовом поддомене до включения на `ts-proxy.com`, с «ок» владельца на действия на сервере.
- Связь с задачей 02 (плашка о неисправностях): тот же Worker мог бы вставлять плашку в страницы через `HTMLRewriter`, без правки сайта. **[вывод]**, решать в задаче 02.
