import { createNavigation } from 'next-intl/navigation';
import { RefObject } from 'react';
import { routing } from './routing';

const mainUrl = process.env.MAIN_URL || 'http://localhost:3000';

export const isExternalLink = (url: string): boolean => {
  if (url.startsWith('/')) {
    return false;
  }

  try {
    const baseOrigin = typeof window !== 'undefined'
      ? window.location.origin
      : mainUrl;
    const parsedUrl = new URL(url, baseOrigin);
    return parsedUrl.origin !== baseOrigin;
  }
  catch {
    return false;
  }
};

export const {
  Link: NextIntlLink,
  redirect,
  usePathname,
  useRouter,
  getPathname,
  permanentRedirect,
} = createNavigation(routing);

type TNextIntlLinkProps = React.AnchorHTMLAttributes<HTMLAnchorElement> & {
  href: string;
  ref?: RefObject<HTMLAnchorElement | HTMLButtonElement | null>;
};

export const Link: FCClass<TNextIntlLinkProps> = ({
  href, target, children, ref, ...props
}) => {
  const computedTarget = target || (isExternalLink(String(href)) ? '_blank' : '_self');
  return (
    <NextIntlLink
      href={href}
      target={computedTarget}
      ref={ref as RefObject<HTMLAnchorElement | null> | undefined}
      {...props}
    >
      {children}
    </NextIntlLink>
  );
};
