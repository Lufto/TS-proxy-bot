import type { Metadata } from 'next';
import {
  createKitMetadata, KitPage, resolveKitVariant, resolveManifestChoice,
} from '@/widgets/kit-variants';
import type { TKitSearchParams } from '@/widgets/kit-variants/model/page';

type TProps = { searchParams: TKitSearchParams };

export async function generateMetadata({ searchParams }: TProps): Promise<Metadata> {
  return createKitMetadata(searchParams);
}

export default async function Kit({ searchParams }: TProps) {
  return (
    <KitPage
      variantId={await resolveKitVariant(searchParams)}
      choice={await resolveManifestChoice(searchParams)}
    />
  );
}
