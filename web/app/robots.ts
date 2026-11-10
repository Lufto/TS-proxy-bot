import type { MetadataRoute } from 'next';
import { SITE_CONFIG } from '@/generated-config';

// Поисковикам сайт открыт целиком (страницу UI-кита убрали 2026-11-11)
export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: '*', allow: '/' },
    sitemap: `${SITE_CONFIG.siteUrl}/sitemap.xml`,
  };
}
