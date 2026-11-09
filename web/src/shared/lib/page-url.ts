import { SITE_CONFIG } from '@/generated-config';

// Путь страницы на языке: русский без приставки, английский — с /en; всегда со слэшем на конце
export const pagePath = (locale: string, slug = ''): string => {
  const prefix = locale === 'ru' ? '' : `/${locale}`;
  return `${prefix}/${slug ? `${slug}/` : ''}`;
};

export const pageUrl = (locale: string, slug = ''): string => `${SITE_CONFIG.siteUrl}${pagePath(locale, slug)}`;
