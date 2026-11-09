'use client';

import { useTranslations } from 'next-intl';
import { useEffect, useState } from 'react';
import { SITE_CONFIG } from '@/generated-config';
import { bannerFor, TBanner, TStatusResult } from '@/shared/lib/status';

const CHECK_EVERY_MS = 60 * 1000;
const TIMEOUT_MS = 10 * 1000;

const fetchStatus = async (): Promise<TStatusResult> => {
  try {
    const response = await fetch(SITE_CONFIG.statusUrl, {
      cache: 'no-store',
      credentials: 'omit',
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (response.status !== 200) {
      return { ok: false };
    }
    return { ok: true, body: await response.json() };
  }
  catch {
    return { ok: false };
  }
};

const MESSAGE_KEY: Record<TBanner, 'statusBotDown' | 'statusMaintenance' | 'statusProxyIssues'> = {
  bot_down: 'statusBotDown',
  maintenance: 'statusMaintenance',
  proxy_issues: 'statusProxyIssues',
};

// Плашка о неполадках (задача сайта 13): раз в минуту спрашивает бота, во вкладке в фоне
// не спрашивает, без интернета у посетителя не показывается — это не наша неполадка.
// Без cookies и счётчиков, как обещано в политике конфиденциальности
export const StatusBanner = () => {
  const t = useTranslations('common');
  const [banner, setBanner] = useState<TBanner | null>(null);

  useEffect(() => {
    const check = async () => {
      if (!navigator.onLine) {
        setBanner(null);
        return;
      }
      setBanner(bannerFor(await fetchStatus()));
    };

    check();
    const timer = setInterval(() => {
      if (!document.hidden) check();
    }, CHECK_EVERY_MS);
    const onVisible = () => {
      if (!document.hidden) check();
    };
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, []);

  if (!banner) {
    return null;
  }

  return (
    <div role="status" className="bg-accent text-on-accent">
      <p className="container t-body flex items-center gap-12 py-10 font-medium">
        <span className="t-label shrink-0 bg-on-accent px-6 py-2 text-accent">!</span>
        {t(MESSAGE_KEY[banner])}
      </p>
    </div>
  );
};
