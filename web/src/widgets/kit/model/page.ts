import type { Metadata } from 'next';
import { faviconDataUri } from './logos';
import { isKitVariantId, KIT_VARIANTS, type TKitVariantId } from './variants';

export type TKitSearchParams = Promise<{ v?: string | string[] }>;

// Вариант из адреса `?v=b`; без него или с неизвестным — первый
export const resolveKitVariant = async (searchParams: TKitSearchParams): Promise<TKitVariantId> => {
  const { v } = await searchParams;
  return isKitVariantId(v) ? v : 'a';
};

export const createKitMetadata = async (searchParams: TKitSearchParams): Promise<Metadata> => {
  const variant = KIT_VARIANTS[await resolveKitVariant(searchParams)];

  return {
    title: `UI-кит: ${variant.name} — TS Proxy`,
    robots: { index: false, follow: false },
    icons: [{ rel: 'icon', url: faviconDataUri(variant), type: 'image/svg+xml' }],
  };
};
