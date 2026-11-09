import { notFound } from 'next/navigation';
import enCommon from '@/data/en/common.json';
import ruCommon from '@/data/ru/common.json';

// Подписи интерфейса по языкам. Обычный импорт, а не чтение папки с диска: так сборщик знает,
// какие файлы нужны, и не тащит весь проект в .next/standalone (задача сайта 16).
// Новый файл в src/data/<язык>/ — добавьте его сюда
const MESSAGES: Record<string, Record<string, unknown>> = {
  ru: { common: ruCommon },
  en: { common: enCommon },
};

export async function loadMessages(locale: string) {
  const messages = MESSAGES[locale];

  if (!messages) {
    notFound();
  }

  return messages;
}
