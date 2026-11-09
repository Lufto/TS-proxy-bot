import '@/assets/styles';
import { metaObject } from '@/app-layer/config/meta';
import { loadMessages } from '@/shared/lib/i18n/loadMessages';
import { routing } from '@/shared/lib/i18n/routing';
import type { Metadata, Viewport } from 'next';
import { SITE_CONFIG } from '@/generated-config';
import { hasLocale, NextIntlClientProvider } from 'next-intl';
import { notFound } from 'next/navigation';
import { PopupsProvider } from '@/app-layer/popups/popups-provider';

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  maximumScale: 1,
  userScalable: false,
};

export const metadata: Metadata = {
  metadataBase: new URL(SITE_CONFIG.siteUrl),
  icons: [
    {
      rel: 'icon',
      url: metaObject.favicon,
      type: 'image/svg+xml',
    },
    {
      rel: 'apple-touch-icon',
      url: '/meta/apple-touch-icon.png',
    },
  ],
  title: metaObject.title,
  description: metaObject.description,
};

export default async function RootLayout({
  children,
  params,
}: Readonly<{
  children: React.ReactNode;
  params: Promise<any>;
}>) {
  const { locale } = await params;

  if (!hasLocale(routing.locales, locale)) {
    notFound();
  }

  const messages = await loadMessages(locale);

  return (
    <html lang={locale}>
      <NextIntlClientProvider locale={locale} messages={messages}>
        <body>
          <PopupsProvider>
            {children}
          </PopupsProvider>
        </body>
      </NextIntlClientProvider>
    </html>
  );
}
