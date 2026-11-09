import type { Metadata } from 'next';
import { findButtonOption, findSecondaryOption } from './buttons';
import { faviconDataUri, findLogoOption } from './logos';
import { isKitVariantId, KIT_VARIANTS, type TKitVariantId } from './variants';

export type TKitSearchParams = Promise<{
  v?: string | string[];
  logo?: string | string[];
  btn?: string | string[];
  btn2?: string | string[];
}>;

// Что примерено в «Манифесте»: знак, главная и вторая кнопки
export type TManifestChoice = {
  logoId: string;
  buttonId: string;
  secondaryId: string;
};

// Вариант из адреса `?v=b`; без него или с неизвестным — выбранный «Манифест»
export const resolveKitVariant = async (searchParams: TKitSearchParams): Promise<TKitVariantId> => {
  const { v } = await searchParams;
  return isKitVariantId(v) ? v : 'c';
};

const single = (value: string | string[] | undefined) => (typeof value === 'string' ? value : '');

// Примерка из адреса `?v=c&logo=3&btn=2&btn2=1`; чего нет или что неизвестно — выбор владельца
export const resolveManifestChoice = async (searchParams: TKitSearchParams): Promise<TManifestChoice> => {
  const { logo, btn, btn2 } = await searchParams;
  return {
    logoId: findLogoOption(single(logo)).id,
    buttonId: findButtonOption(single(btn)).id,
    secondaryId: findSecondaryOption(single(btn2)).id,
  };
};

export const createKitMetadata = async (searchParams: TKitSearchParams): Promise<Metadata> => {
  const variant = KIT_VARIANTS[await resolveKitVariant(searchParams)];

  return {
    title: `UI-кит: ${variant.name} — TS Proxy`,
    robots: { index: false, follow: false },
    icons: [{
      rel: 'icon',
      url: faviconDataUri(variant, (await resolveManifestChoice(searchParams)).logoId),
      type: 'image/svg+xml',
    }],
  };
};
