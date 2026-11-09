import type { Metadata } from 'next';
import { AppLayout } from '@/app-layer/layout';
import { KitPage } from '@/widgets/kit';

export const metadata: Metadata = {
  title: 'UI-кит — TS Proxy',
  robots: { index: false, follow: false },
};

export default function Kit() {
  return (
    <AppLayout>
      <KitPage />
    </AppLayout>
  );
}
