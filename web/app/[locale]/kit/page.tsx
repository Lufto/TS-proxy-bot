import type { Metadata } from 'next';
import { createKitMetadata, KitPage, resolveKitVariant } from '@/widgets/kit';
import type { TKitSearchParams } from '@/widgets/kit/model/page';

type TProps = { searchParams: TKitSearchParams };

export async function generateMetadata({ searchParams }: TProps): Promise<Metadata> {
  return createKitMetadata(searchParams);
}

export default async function Kit({ searchParams }: TProps) {
  return <KitPage variantId={await resolveKitVariant(searchParams)} />;
}
