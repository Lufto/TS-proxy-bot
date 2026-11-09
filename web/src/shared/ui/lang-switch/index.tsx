'use client';

import cx from 'classix';
import { useLocale } from 'next-intl';
import { NextIntlLink, usePathname } from '@/shared/lib/i18n/navigation';
import { routing } from '@/shared/lib/i18n/routing';

// Переключатель языка: ведёт на ту же страницу на другом языке
export const LangSwitch: FCClass<{ label: string }> = ({ label, className }) => {
  const locale = useLocale();
  const pathname = usePathname();

  return (
    <nav className={cx('flex items-center gap-4', className)} aria-label={label}>
      {routing.locales.map(item => (
        <NextIntlLink
          key={item}
          href={pathname}
          locale={item}
          aria-current={item === locale ? 'true' : undefined}
          className={cx(
            't-label focus-ring px-6 py-4 transition-colors duration-200',
            item === locale ? 'bg-cream text-on-accent' : 'hover:text-cream',
          )}
        >
          {item}
        </NextIntlLink>
      ))}
    </nav>
  );
};
