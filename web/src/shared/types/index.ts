import { RES_STATUS } from '../enums';

export type Locale = 'ru' | 'en';

export type TBackResponse<T = Record<string, any>> = {
  status: RES_STATUS;
  data?: T;
};

export type TGetNewFlat = {
  page: number;
} & Record<string, any>;

export type TSearchParams = Record<string, string | undefined>;

export type TParams = Promise<Record<string, string>>;

export type PropsPage = {
  params: Promise<{
    locale: string;
    slug?: string[];
  }>;
};
