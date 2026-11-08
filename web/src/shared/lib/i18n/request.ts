import { getRequestConfig } from 'next-intl/server';
import { hasLocale } from 'next-intl';
import { routing } from './routing';
import { loadMessages } from './loadMessages';

export default getRequestConfig(async ({ requestLocale }) => {
  const currentLocale = await requestLocale;

  const locale = hasLocale(routing.locales, currentLocale)
    ? currentLocale
    : routing.defaultLocale;

  const messages = await loadMessages(locale);

  return {
    locale,
    messages,
  };
});
