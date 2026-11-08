import { TSeoSchema } from '../model/schemas/seo';
import { TBackResponse } from '../types';

export const getSeoData = async (seoApi: () => Promise<false | TBackResponse<{ seo?: TSeoSchema }>>) => {
  const res = await seoApi();

  if (res && res.status === 'success') {
    const { data } = res;
    const seo = data?.seo;

    if (seo) {
      return {
        ...seo,
        openGraph: {
          ...seo,
          type: 'website',
        },
        twitter: {
          ...seo,
        },
      };
    }
  }

  return {};
};
