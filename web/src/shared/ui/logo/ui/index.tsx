import cx from 'classix';

type TLogo = {
  /** Сторона знака в пикселях; надпись масштабируется от неё */
  size?: number;
  /** Без надписи — только знак */
  wordmark?: boolean;
};

// Знак «Сдвиг» (выбор владельца, задача сайта 05): буква T на чёрном квадрате с оранжевой
// «тенью-сдвигом». Та же разметка лежит в public/meta/favicon.svg
export const LogoMark: FCClass<{ size?: number }> = ({ size = 28, className }) => (
  <svg
    className={cx('shrink-0', className)}
    width={size}
    height={size}
    viewBox="0 0 32 32"
    aria-hidden
  >
    <rect x="9" y="9" width="23" height="23" fill="var(--color-accent)" />
    <rect x="1" y="1" width="22" height="22" fill="var(--color-on-accent)" stroke="var(--color-accent)" strokeWidth="2" />
    <rect x="5" y="6" width="14" height="4" fill="var(--color-accent)" />
    <rect x="10" y="10" width="4" height="9" fill="var(--color-accent)" />
  </svg>
);

export const Logo: FCClass<TLogo> = ({ size = 28, wordmark = true, className }) => (
  <span className={cx('inline-flex items-center', className)} style={{ gap: size * 0.4 }}>
    <LogoMark size={size} />
    {wordmark && (
      <span
        className="font-display font-extrabold whitespace-nowrap text-cream"
        style={{ fontSize: size * 0.72, lineHeight: 1, letterSpacing: '-0.01em' }}
      >
        TS PROXY
      </span>
    )}
  </span>
);
