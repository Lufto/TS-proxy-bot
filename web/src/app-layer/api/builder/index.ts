import { api } from '@/shared/api';
import { validationUtils } from '@/shared/lib/validation-utils';
import { builderPageSchema } from '@/app-layer/model/builder';

// Данные страниц лежат по языкам: /ru/constructor/page/terms → apiStatic/ru/constructor/page/terms.ts
export const getPageBuilderData = async (locale: string, slug: string = '') => {
  const endpoint = `/${locale}/constructor/page/${slug}`;

  const data = await api.get({ endpoint });
  return validationUtils(builderPageSchema, data, endpoint);
};
