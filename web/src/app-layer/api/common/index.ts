'use server';

import { api } from '@/shared/api';
import { validationUtils } from '@/shared/lib/validation-utils';
import { commonSchema } from '@/app-layer/model/common';

export const getCommonData = async (locale: string) => {
  const endpoint = `/${locale}/common`;
  const data = await api.get({ endpoint });

  return validationUtils(commonSchema, data, endpoint);
};
