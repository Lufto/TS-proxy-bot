// Ссылки в Telegram-бота @ts_proxy_creator_bot (владелец, 2026-11-09).
// Переменная NEXT_PUBLIC_BOT_USERNAME нужна, только чтобы проверить сайт с другим ботом
const BOT_USERNAME = process.env.NEXT_PUBLIC_BOT_USERNAME || 'ts_proxy_creator_bot';

export const BOT_URL = `https://t.me/${BOT_USERNAME}`;

// `?start=support` открывает в боте сразу поддержку (задача сайта 07)
export const BOT_SUPPORT_URL = `${BOT_URL}?start=support`;
