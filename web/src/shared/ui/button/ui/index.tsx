import cx from 'classix';
import { Link } from '@/shared/lib/i18n/navigation';
import { TButton } from '../types';
import styles from './button.module.scss';

// Кнопка кита. С адресом — ссылка, без адреса — кнопка (как «Ссылка» из хранилища компонентов).
// Надпись-строка дублируется в data-label: из неё CSS собирает «Бегущую строку»
export const Button: FCClass<TButton> = ({
  className,
  children,
  variant = 'primary',
  size = 'l',
  url,
  target,
  arrow = variant === 'primary',
  disabled,
  submit,
  onClick,
}) => {
  const elementClass = cx(
    styles.button,
    styles[size],
    styles[variant],
    disabled && styles.disabled,
    className,
  );

  const body = (
    <>
      <span className={styles.label} data-label={typeof children === 'string' ? children : undefined}>
        <span>{children}</span>
      </span>
      {arrow && <span className={styles.arrow} aria-hidden>→</span>}
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
      <Link
        href={url}
        target={target}
        rel={url.startsWith('http') ? 'noopener noreferrer' : undefined}
        className={elementClass}
        onClick={onClick}
      >
        {body}
      </Link>
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
