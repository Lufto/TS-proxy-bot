import { getTranslations } from 'next-intl/server';
import { Link } from '@/shared/lib/i18n/navigation';

export default async function NotFound() {
  const t = await getTranslations('common');

  return (
    <main>
      <h1>{t('notFoundTitle')}</h1>
      <p>{t('notFoundText')}</p>
      <Link href="/">{t('toMain')}</Link>
    </main>
  );
}
