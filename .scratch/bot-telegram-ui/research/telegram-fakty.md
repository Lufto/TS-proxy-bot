# Что Telegram позволяет боту

Ответ на задачу `.scratch/bot-telegram-ui/issues/01-telegram-fakty.md`. Проверено 2026-11-18 по документации **Bot API 10.4** (вышла 9 ноября 2026, последняя на эту дату). Где в документации ответа нет, смотрел исходники сервера Bot API (`tdlib/telegram-bot-api`) и библиотеки TDLib, на которой он работает (ветка `master` на 2026-11-18).

Пометки:
- **[док]** — прямо написано в документации Telegram;
- **[код]** — так написано в исходниках сервера Bot API / TDLib, в документации этого нет;
- **[вывод]** — наш вывод, надо проверить на тестовом боте.

## 1. Удаление сообщений (`deleteMessage` / `deleteMessages`)

- **48 часов — да.** «A message can only be deleted if it was sent less than 48 hours ago». Бот может удалять и свои, и входящие сообщения в личном чате [док]. https://core.telegram.org/bots/api#deletemessage
- **Документация тексты ошибок не перечисляет**: там сказано только, что при ошибке `ok=false`, а в `description` — текст для человека [док]. https://core.telegram.org/bots/api#making-requests
- **Сообщение старше 48 часов** → `400 Bad Request: message can't be deleted for everyone` [код]. TDLib для бота проверяет срок (2 × 86400 с) и отдаёт «Message can't be deleted for everyone»; сервер Bot API делает первую букву строчной и добавляет «Bad Request: ». https://github.com/tdlib/td/blob/master/td/telegram/MessagesManager.cpp (функции `can_revoke_message`, `delete_messages`), https://github.com/tdlib/telegram-bot-api/blob/master/telegram-bot-api/Client.cpp (`fail_query_with_error`)
- **Сообщение уже удалено / нет такого** → `deleteMessage` отвечает `400 Bad Request: message to delete not found` [код]. `deleteMessages` такие сообщения пропускает и возвращает `True` [док: «If some of the specified messages can't be found, they are skipped»]. https://core.telegram.org/bots/api#deletemessages
- **Осторожно с `deleteMessages`**: если в пачке есть хоть одно сообщение старше 48 часов, по коду TDLib ошибку получает **вся пачка** и не удаляется ничего [код, вывод — проверить]. Надёжнее удалять живое меню по одному через `deleteMessage`.
- Ещё одна редкая ошибка: `400 Bad Request: message can't be deleted` (вообще нельзя удалить, например служебное сообщение) [код].

## 2. Убрать кнопки (`editMessageReplyMarkup`) у старого сообщения

- **Срока нет.** В документации предел 48 часов указан только для бизнес-сообщений, которые отправил не бот [док]. https://core.telegram.org/bots/api#editmessagereplymarkup
- В TDLib для своих (исходящих) сообщений бота ограничение по времени на правку выключено [код: `can_edit_message`, `has_edit_time_limit = !(is_bot && m->is_outgoing) …`]. Значит, кнопки можно снять и через неделю, и через месяц.
- Возможные ответы: если кнопок уже нет — `400 Bad Request: message is not modified: …` (это безопасно игнорировать) [код: `MESSAGE_NOT_MODIFIED` в `Client.cpp`]; если сообщения нет — `400 Bad Request: message to edit not found` [код].
- Править можно только сообщения без клавиатуры или с инлайн-кнопками [док]. https://core.telegram.org/bots/api#updating-messages

## 3. Поднимается ли чат в списке от правки

- **Подтверждаю: поднимает только новое сообщение, правка — нет.** Прямой фразы в Bot API нет. В TDLib место чата в списке считается по **дате отправки** последнего сообщения (`m->date`), дата правки не участвует [код: `update_dialog_pos` в `MessagesManager.cpp`]. В MTProto у чата есть только `top_message` — номер последнего сообщения [док]. https://core.telegram.org/constructor/dialog
- Попутно [код]: если удалить последнее сообщение, чат **не опускается** вниз — TDLib запоминает время удаления. Так что «новое меню + удалить старое» чат поднимет, а удаление само ничего не испортит.

## 4. Сообщение с картинкой

- **Подпись — до 1024 символов** (после разбора разметки) [док]. Текстовое сообщение — до 4096. https://core.telegram.org/bots/api#sendphoto
- Картинка: до 10 МБ, ширина + высота ≤ 10000, соотношение сторон не больше 20 [док]. Подпись можно показать над картинкой (`show_caption_above_media`) [док].
- **Подпись и кнопки править можно**: `editMessageCaption` (подпись + `reply_markup`), `editMessageReplyMarkup` (только кнопки), `editMessageMedia` (заменить саму картинку). https://core.telegram.org/bots/api#editmessagecaption, https://core.telegram.org/bots/api#editmessagemedia
- **Текстовое сообщение превратить в фото — можно** через `editMessageMedia` («or to replace a text or a rich message with a media») — с Bot API 7.11 [док]. Обратно (фото → текст) — нет: `editMessageText` правит только текстовые сообщения [док]. https://core.telegram.org/bots/api-changelog#october-31-2024
- Для нашего «живого меню» это неважно: каждый экран всё равно приходит новым сообщением.

## 5. Профиль бота: API или BotFather

| Что | Через API | Только BotFather |
|---|---|---|
| Описание в пустом чате («Что умеет этот бот?»), до 512 симв. | `setMyDescription` [док] | — |
| Короткое описание (профиль, при пересылке ссылки), до 120 симв. | `setMyShortDescription` [док] | — |
| Имя бота | `setMyName` [док] | — |
| **Аватар** | **`setMyProfilePhoto` / `removeMyProfilePhoto`** — с Bot API 9.4 (9 февраля 2026); статичное фото `.jpg` или анимированное [док] | или `/setuserpic` |
| **Картинка/видео описания** (видна в пустом чате до `/start`) | **нет такого метода** | «Edit Description Picture» в @BotFather [док] |
| Команды в меню | `setMyCommands` [док] | — |

Источники: https://core.telegram.org/bots/api#setmyprofilephoto, https://core.telegram.org/bots/api#setmydescription, https://core.telegram.org/bots/api#setmyshortdescription, https://core.telegram.org/bots/features#about-text-description-and-profile-media, https://core.telegram.org/bots/api-changelog#february-9-2026

Итог: аватар бот может поставить сам, но владельцу проще загрузить его в BotFather один раз. Картинку описания — **только владелец через BotFather**.

## 6. Новые возможности оформления (полезные для экранов)

- **Цветные кнопки** — поле `style` у кнопки: `danger` (красная), `success` (зелёная), `primary` (синяя); без поля — обычный цвет приложения. Bot API 9.4 [док]. Работает без условий. https://core.telegram.org/bots/api#inlinekeyboardbutton
- **Иконка-эмодзи на кнопке** — `icon_custom_emoji_id`, Bot API 9.4. **Условие**: бот купил доп. имя на Fragment **или** у владельца бота Telegram Premium [док]. Без этого не сработает.
- **Неактивная кнопка** — поле `disabled` (`DisabledButton`), кнопка видна, но ничего не делает. Bot API 10.3 [док]. Можно, например, показать «Оплата временно недоступна».
- **Кнопка «скопировать текст»** — `copy_text`, Bot API 7.11 [док]. Удобно для ключа/ссылки подписки.
- **Превью ссылки** — `link_preview_options`: выбрать, какую ссылку показывать превью, крупная/мелкая картинка (`prefer_large_media`/`prefer_small_media`), превью над текстом (`show_above_text`), или отключить (`is_disabled`). Bot API 7.0 [док]. https://core.telegram.org/bots/api#linkpreviewoptions
- **Rich messages** (`sendRichMessage`, Bot API 10.1–10.3) — сообщения с заголовками, списками, таблицами, сворачиваемыми цитатами, кнопками внутри [док]. Мощно, но новое; наша библиотека (aiogram) может ещё не поддерживать — **[вывод: проверить]**.
- **Временные (ephemeral) ответы на нажатие** — с 10.4 в любом чате, включая личный; `replace_callback_query_message` показывает такой ответ **на месте** исходного сообщения [док]. Не гарантируется, что клиент его получит, если он не в сети [док]. Для живого меню не подходит (нам нужно именно новое сообщение, чтобы чат поднимался), но можно держать в уме.
- Виджеты (`sendWidget`, 10.4) — завязаны на TON, нам не нужны.

Источник по версиям: https://core.telegram.org/bots/api-changelog
