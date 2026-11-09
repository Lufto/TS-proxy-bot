import { getTranslations } from 'next-intl/server';
import { AppLayout } from '@/app-layer/layout';
import { Button } from '@/shared/ui/button';

export default async function NotFound() {
  const t = await getTranslations('common');

  return (
    <AppLayout>
      <main className="container flex flex-col items-start gap-24 py-80 t:py-120">
        <span className="t-label text-accent">404</span>
        <h1 className="t-h1">{t('notFoundTitle')}</h1>
        <p className="t-body-l text-muted">{t('notFoundText')}</p>
        <Button url="/">{t('toMain')}</Button>
      </main>
    </AppLayout>
  );
}
