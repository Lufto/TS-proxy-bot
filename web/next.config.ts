import type { NextConfig } from 'next';
import createNextIntlPlugin from 'next-intl/plugin';
import process from 'node:process';
import type { RemotePattern } from 'next/dist/shared/lib/image-config';
import { SITE_CONFIG } from './src/generated-config';

const generateApiUrl = (pathEnv?: string) => {
  const apiPath = process.env.HTTP_CLIENT === 'mock' ? '' : '/api';
  return `${pathEnv}${apiPath}`;
};

const getRemotePatterns = (api?: string): RemotePattern => {
  if (typeof api !== 'string') {
    return {
      hostname: 'localhost',
      pathname: '**',
    };
  }

  const url = new URL(api);

  return {
    hostname: url.hostname,
    pathname: '**',
  };
};

const isMock = process.env.HTTP_CLIENT === 'mock';

const nextConfig: NextConfig = {
  turbopack: {
    resolveAlias: isMock
      ? {}
      : {
          '@/generated': './src/shared/api/empty-mock.ts',
        },
  },
  reactStrictMode: true,
  trailingSlash: true,
  env: {
    API: generateApiUrl(process.env.API),
  },
  sassOptions: {
    additionalData: `
      @use "~/src/assets/styles/lib/index.scss" as *;
    `,
  },
  images: {
    deviceSizes: [400, 768, 1024, 1280, 1920, 2048],
    qualities: [100, 75],
    remotePatterns: [
      getRemotePatterns(process.env.API),
    ],
  },
  logging: {
    fetches: {
      fullUrl: true,
    },
  },
  // Старые адреса сайта на чистом HTML: тарифы теперь на главной, поддержка — в боте
  async redirects() {
    const supportUrl = `https://t.me/${SITE_CONFIG.botUsername}?start=support`;
    return [
      // trailingSlash: сначала Next добавляет слэш, поэтому источники — со слэшем
      { source: '/tariffs/', destination: '/#tariffs', permanent: true },
      { source: '/en/tariffs/', destination: '/en/#tariffs', permanent: true },
      { source: '/support/', destination: supportUrl, permanent: false },
      { source: '/en/support/', destination: supportUrl, permanent: false },
    ];
  },
  async rewrites() {
    // const currentHttp = process.env.IS_HTTPS === 'true' ? 'https://' : 'http://';

    return [
    ];
  },
};

const withNextIntl = createNextIntlPlugin(
  './src/shared/lib/i18n/request.ts',
);

export default withNextIntl(nextConfig);
