import fs from 'fs';
import path from 'path';
import { notFound } from 'next/navigation';
import { PATH_DATA_LOCALE } from '@/shared/enums';

export async function loadMessages(locale: string) {
  const dataPath = path.join(process.cwd(), PATH_DATA_LOCALE, locale);

  if (!fs.existsSync(dataPath)) {
    notFound();
  }

  try {
    const files = fs.readdirSync(dataPath).filter(file => file.endsWith('.json'));
    const messages: Record<string, any> = {};

    for (const file of files) {
      const filePath = path.join(dataPath, file);
      const content = fs.readFileSync(filePath, 'utf-8');
      const namespace = path.basename(file, '.json');
      messages[namespace] = JSON.parse(content);
    }

    return messages;
  }
  catch {
    notFound();
  }
}
