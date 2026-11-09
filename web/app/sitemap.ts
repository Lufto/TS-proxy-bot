import type { MetadataRoute } from 'next';
import { SITE_PAGES } from '@/shared/config/pages';
import { pageUrl } from '@/shared/lib/page-url';

// Карта сайта: каждая страница на обоих языках, со ссылками на языковые версии друг друга
export default function sitemap(): MetadataRoute.Sitemap {
  return SITE_PAGES.flatMap(slug => ['ru', 'en'].map(locale => ({
    url: pageUrl(locale, slug),
    changeFrequency: 'monthly' as const,
    priority: slug === '' ? 1 : 0.6,
    alternates: {
      languages: {
        ru: pageUrl('ru', slug),
        en: pageUrl('en', slug),
      },
    },
  })));
}
