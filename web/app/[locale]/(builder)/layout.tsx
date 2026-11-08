import { ReactNode } from 'react';
import { AppLayout } from '@/app-layer/layout';

export default async function Layout({
  children,
}: {
  children: ReactNode;
}) {
  return (
    <AppLayout>
      {children}
    </AppLayout>
  );
}
