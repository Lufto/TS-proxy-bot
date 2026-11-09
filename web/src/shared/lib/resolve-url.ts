import { BOT_SUPPORT_URL, BOT_URL } from '@/shared/config/bot';

// Адреса в текстах apiStatic: «bot» и «support» — ссылки в бота из общего конфига,
// остальное — как есть. Так имя бота не вписано руками ни в одну страницу
export const resolveUrl = (url: string): string => {
  if (url === 'bot') return BOT_URL;
  if (url === 'support') return BOT_SUPPORT_URL;
  return url;
};
