import type { Metadata } from 'next';
import type { ComponentType } from 'react';
import { blockComponent } from '@/app-layer/config/block-component';
import { notFound } from 'next/navigation';
import { PropsPage } from '@/shared/types';
import { RES_STATUS } from '@/shared/enums';
import { getPageBuilderData } from '@/app-layer/api/builder';
import { getSeoData } from '@/shared/api/helpers';
import { pagePath } from '@/shared/lib/page-url';

const OG_IMAGE = '/meta/og.png';

export const createMetadata = async (params: PropsPage): Promise<Metadata> => {
  const routeData = await params.params;

  if (/\.[a-z0-9]+$/i.test(routeData.slug?.join('/') ?? '')) {
    notFound();
  }

  const getPageDataWrapper = async () => {
    return getPageBuilderData(routeData.locale, routeData.slug?.join('/'));
  };

  const seo: Metadata = await getSeoData(getPageDataWrapper);
  const slug = routeData.slug?.join('/') ?? '';

  // Языковые версии друг друга и картинка превью для Telegram (задача сайта 12)
  return {
    ...seo,
    alternates: {
      canonical: pagePath(routeData.locale, slug),
      languages: {
        'ru': pagePath('ru', slug),
        'en': pagePath('en', slug),
        'x-default': pagePath('ru', slug),
      },
    },
    openGraph: {
      ...seo.openGraph,
      url: pagePath(routeData.locale, slug),
      siteName: 'TS Proxy',
      locale: routeData.locale === 'ru' ? 'ru_RU' : 'en_US',
      images: [{ url: OG_IMAGE, width: 1200, height: 630, alt: 'TS Proxy' }],
    },
    twitter: {
      ...seo.twitter,
      card: 'summary_large_image',
      images: [OG_IMAGE],
    },
  };
};

const BuilderPage = async (params: PropsPage) => {
  const routeData = await params.params;

  if (/\.[a-z0-9]+$/i.test(routeData.slug?.join('/') ?? '')) {
    notFound();
  }

  const res = await getPageBuilderData(routeData.locale, routeData.slug?.join('/'));
  if (!res || res.status !== RES_STATUS.success) {
    notFound();
  }

  const content = res.data?.content;

  if (!content) return null;

  return (
    <main>
      {content.map((block) => {
        const blockConfig = blockComponent[block.type];
        if (!blockConfig) return null;

        const Component = blockConfig as ComponentType<typeof block.data>;
        return <Component key={block.uuid} {...block.data} />;
      })}
    </main>
  );
};

export default BuilderPage;
