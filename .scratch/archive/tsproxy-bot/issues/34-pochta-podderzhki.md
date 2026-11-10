# Почта поддержки support@ts-proxy.com

Type: task
Status: done

## Что сделать

Владелец (2026-10-30): на сайте и в документах вместо личного `tsarewalex2006@gmail.com` — адрес `support@ts-proxy.com`, который пересылает письма на этот Gmail. Так личная почта не светится публично. Сделать **до подачи сайта на согласование в Platega** (задача 25).

Шаги (Cloudflare Email Routing, бесплатно, ~10 минут; делает владелец, агент подскажет по шагам):
1. Cloudflare → домен `ts-proxy.com` → **Email** → **Email Routing** → включить. Cloudflare сам добавит записи MX и SPF. Если у домена уже есть свои MX-записи — сначала показать агенту.
2. **Destination addresses** → добавить `tsarewalex2006@gmail.com` → подтвердить письмом, которое придёт в Gmail.
3. **Routing rules** → Custom address `support` → действие «Send to» → Gmail.
4. Проверить: написать на `support@ts-proxy.com` с другой почты — письмо пришло в Gmail.

**Отвечать от имени support@.** Email Routing только принимает письма. Если ответить из Gmail как обычно, клиент увидит личный адрес. Чтобы отвечать от `support@ts-proxy.com`, нужен Gmail «Отправлять письма как» и SMTP-сервер — например, бесплатный тариф Brevo. Сделать вместе с агентом, если письма в поддержку будут приходить; до этого — отвечать в Telegram-боте.

## Готово, когда

- Письмо на `support@ts-proxy.com` доходит до Gmail.
- В документах и на сайте (задача 25) стоит `support@ts-proxy.com`; это же — контакт для Platega.

Связано: задача 25, `../vpn-bot-rework/issues/17-vybor-oplaty-i-yur-oformlenie.md`.

## Comments

- 2026-10-31: владелец настроил пересылку `support@ts-proxy.com` → Gmail. Агент заменил адрес на всех страницах `site/` (README обновлён). Ответы от имени support@ (Brevo) — пока не делаем, отвечаем в боте.
