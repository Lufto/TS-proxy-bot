import { Fragment } from 'react';
import { SUPPORT_EMAIL } from '@/shared/config/bot';
import { resolveUrl } from '@/shared/lib/resolve-url';
import { Link, LinkUnderline } from '@/shared/ui/link';

// Строка из apiStatic с простой разметкой: [текст](адрес) — ссылка, {email} — почта поддержки
// из общего конфига. Больше ничего не разбирает: тексты пишутся без HTML
const TOKEN = /\[([^\]]+)\]\(([^)]+)\)|\{email\}/g;

export const RichText: FCClass<{ text: string }> = ({ text }) => {
  const parts: React.ReactNode[] = [];
  let last = 0;

  for (const match of text.matchAll(TOKEN)) {
    parts.push(text.slice(last, match.index));
    const [whole, label, url] = match;
    const href = whole === '{email}' ? `mailto:${SUPPORT_EMAIL}` : resolveUrl(url);
    parts.push(
      <Link key={match.index} url={href} target={href.startsWith('mailto:') ? '_self' : undefined} underline={LinkUnderline.solid}>
        {whole === '{email}' ? SUPPORT_EMAIL : label}
      </Link>,
    );
    last = match.index + whole.length;
  }
  parts.push(text.slice(last));

  return <>{parts.map((part, index) => <Fragment key={index}>{part}</Fragment>)}</>;
};
