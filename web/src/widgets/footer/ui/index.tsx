import type { TFooterData } from '@/app-layer/model/common';
import { BOT_SUPPORT_URL, BOT_URL } from '@/shared/config/bot';
import { Button } from '@/shared/ui/button';
import { Link, LinkColor } from '@/shared/ui/link';
import { Logo } from '@/shared/ui/logo';

// Подвал всех страниц: кнопки в бота и в поддержку, документы, почта
export const Footer: FCClass<{ data: TFooterData }> = ({ data }) => (
  <footer className="mt-auto border-t border-line">
    <div className="container flex flex-col gap-40 py-48 t:py-64">
      <div className="flex flex-col justify-between gap-32 md:flex-row md:items-end">
        <div className="flex max-w-420 flex-col gap-16">
          <Logo size={32} />
          <p className="t-body text-muted">{data.tagline}</p>
        </div>
        <div className="flex flex-wrap gap-12">
          <Button url={BOT_URL}>{data.cta}</Button>
          <Button variant="secondary" url={BOT_SUPPORT_URL}>{data.support}</Button>
        </div>
      </div>

      <div className="rule flex flex-col justify-between gap-20 pt-24 t:flex-row t:items-center">
        <nav className="flex flex-wrap gap-x-24 gap-y-12">
          {data.links.map(item => (
            <Link key={item.url} url={item.url} color={LinkColor.muted} typography="t-caption">
              {item.title}
            </Link>
          ))}
        </nav>
        <div className="flex flex-wrap items-center gap-x-24 gap-y-8">
          <Link url={`mailto:${data.email}`} target="_self" color={LinkColor.muted} typography="t-caption">
            {data.email}
          </Link>
          <span className="t-caption">
            ©
            {' '}
            {new Date().getFullYear()}
            {' '}
            {data.copyright}
          </span>
        </div>
      </div>
    </div>
  </footer>
);
