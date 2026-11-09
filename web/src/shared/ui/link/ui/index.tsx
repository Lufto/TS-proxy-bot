import cx from 'classix';
import { Link as NextLink } from '@/shared/lib/i18n/navigation';
import { LinkColor, LinkUnderline, TLink } from '../types';

const COLOR_CLASS: Record<LinkColor, string> = {
  [LinkColor.cream]: 'text-cream hover:text-accent',
  [LinkColor.muted]: 'text-muted hover:text-cream',
  [LinkColor.accent]: 'text-accent',
};

const UNDERLINE_CLASS: Record<LinkUnderline, string> = {
  [LinkUnderline.hover]: cx(
    'relative',
    'after:absolute after:-bottom-2 after:left-0 after:h-1 after:w-full after:bg-current',
    'after:origin-right after:scale-x-0 after:transition-transform after:duration-500 after:ease-snap',
    'hover:after:origin-left hover:after:scale-x-100',
  ),
  [LinkUnderline.solid]: 'underline decoration-accent decoration-1 underline-offset-4',
  [LinkUnderline.none]: '',
};

// Ссылка из хранилища компонентов (`link`, исходно ascot-front) в стиле «Манифеста».
// С адресом рендерится ссылка, без адреса — кнопка. Цвет и шрифт по умолчанию наследуются
export const Link: FCClass<TLink> = ({
  className,
  children,
  underline = LinkUnderline.hover,
  color = LinkColor.cream,
  arrow,
  typography = '',
  url,
  target,
  disabled,
  submit,
  rel,
  onClick,
}) => {
  const elementClass = cx(
    'group inline-flex w-fit cursor-pointer items-center gap-8 transition-colors duration-200',
    'focus-ring',
    COLOR_CLASS[color],
    UNDERLINE_CLASS[underline],
    typography,
    disabled && 'pointer-events-none opacity-50',
    className,
  );

  const body = (
    <>
      {children}
      {arrow && (
        <span className="transition-transform duration-200 group-hover:translate-x-4" aria-hidden>→</span>
      )}
    </>
  );

  // Отключённая ссылка: без href не фокусируется и не активируется, role и aria-disabled — для скринридера
  if (url && disabled) {
    return (
      <a role="link" aria-disabled="true" className={elementClass}>
        {body}
      </a>
    );
  }

  if (url) {
    return (
      <NextLink
        href={url}
        target={target}
        rel={rel ?? (url.startsWith('http') ? 'noopener noreferrer' : undefined)}
        className={elementClass}
        onClick={onClick}
      >
        {body}
      </NextLink>
    );
  }

  return (
    <button
      type={submit ? 'submit' : 'button'}
      className={elementClass}
      disabled={disabled}
      onClick={onClick}
    >
      {body}
    </button>
  );
};
