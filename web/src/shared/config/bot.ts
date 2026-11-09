import { SITE_CONFIG } from '@/generated-config';

// Ссылки в Telegram-бота; имя бота — из config/site.yaml
export const BOT_URL = `https://t.me/${SITE_CONFIG.botUsername}`;

// `?start=support` открывает в боте сразу поддержку (задача сайта 07)
export const BOT_SUPPORT_URL = `${BOT_URL}?start=support`;

export const SUPPORT_EMAIL = SITE_CONFIG.supportEmail;
