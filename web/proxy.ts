import { NextRequest } from 'next/server';
import createMiddleware from 'next-intl/middleware';
import { routing } from '@/shared/lib/i18n/routing';

export default async function middleware(req: NextRequest) {
  const handleI18nRouting = createMiddleware(routing);

  return handleI18nRouting(req);
}

export const config = {
  matcher: ['/((?!api|_next|_vercel|.*\\..*).*)', '/([\\w-]+)?/users/(.+)'],
};
