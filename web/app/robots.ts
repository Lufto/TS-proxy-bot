import type { MetadataRoute } from 'next';
import { SITE_CONFIG } from '@/generated-config';

// Поисковикам сайт открыт, кроме UI-кита (задача сайта 12)
export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: '*', allow: '/', disallow: ['/kit/', '/en/kit/'] },
    sitemap: `${SITE_CONFIG.siteUrl}/sitemap.xml`,
  };
}
