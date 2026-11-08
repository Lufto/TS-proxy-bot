import type { Metadata } from 'next';
import BuilderPage, { createMetadata } from '@/pages/builder';
import { PropsPage } from '@/shared/types';

export async function generateMetadata(params: PropsPage): Promise<Metadata> {
  return createMetadata(params);
}

export default BuilderPage;
