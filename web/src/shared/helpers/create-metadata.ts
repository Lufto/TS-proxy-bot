import { Metadata } from 'next';
import { OpenGraphType } from 'next/dist/lib/metadata/types/opengraph-types';
import { getTranslations } from 'next-intl/server';
import { TTwitterCardType } from '@/shared/model/schemas/seo';

type TCreateMetada = {
  fetchData?: (...args: any[]) => Promise<any>;
  pageName?: string;
  additionalTitle?: string;
};

export const createMetadata = async (params: TCreateMetada): Promise<Metadata> => {
  const { fetchData, pageName, additionalTitle } = params;
  const t = await getTranslations('common');

  let fetchObj = {};
  let staticObj = {};
  let additionalObj = {};

  if (fetchData) {
    const data = await fetchData();

    if (data && data.seo) {
      const obj = {
        title: data.seo?.title,
        description: data.seo?.description,
        keywords: data.seo?.keywords,
        robots: data.seo?.robots,
        images: data.seo?.image && [{ url: data.seo.image }],
      };

      fetchObj = {
        ...obj,
        openGraph: {
          ...obj,
          type: data.seo?.type as OpenGraphType || 'website',
        },
        twitter: {
          ...obj,
          card: data.seo?.card as TTwitterCardType,
        },
      };
    }
  }

  if (pageName) {
    const staticData = await getTranslations(pageName);
    const obj = {
      title: staticData('title'),
      description: staticData('description'),
    };

    staticObj = {
      ...obj,
      openGraph: {
        ...obj,
      },
      twitter: {
        ...obj,
      },
    };
  }

  if (additionalTitle) {
    const obj = {
      title: additionalTitle,
    };

    additionalObj = {
      ...obj,
      openGraph: {
        ...obj,
      },
      twitter: {
        ...obj,
      },
    };
  }

  return {
    ...fetchObj,
    ...staticObj,
    ...additionalObj,
    metadataBase: new URL(process.env.MAIN_URL || 'http://localhost:3000'),
    openGraph: {
      siteName: t('siteName'),
    },
  };
};
