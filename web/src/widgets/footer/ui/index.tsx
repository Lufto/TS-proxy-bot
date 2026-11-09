import type { TFooterData } from '@/app-layer/model/common';
import { BOT_SUPPORT_URL, SUPPORT_EMAIL } from '@/shared/config/bot';
import { Link, LinkColor } from '@/shared/ui/link';
import { Logo } from '@/shared/ui/logo';

// Подвал всех страниц: документы, поддержка и почта. Кнопок нет — их дают шапка и блок-призыв над подвалом
export const Footer: FCClass<{ data: TFooterData }> = ({ data }) => (
  <footer className="mt-auto border-t border-line">
    <div className="container flex flex-col gap-40 py-48 t:py-64">
      <div className="flex max-w-420 flex-col gap-16">
        <Logo size={32} />
        <p className="t-body text-muted">{data.tagline}</p>
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
          <Link url={BOT_SUPPORT_URL} color={LinkColor.muted} typography="t-caption">
            {data.support}
          </Link>
          <Link url={`mailto:${SUPPORT_EMAIL}`} target="_self" color={LinkColor.muted} typography="t-caption">
            {SUPPORT_EMAIL}
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
