import cx from 'classix';
import styles from './burger.module.css';

export type TBurgerOpenClosed = 'open' | 'closed';

export type TBurger = {
  state?: TBurgerOpenClosed;
  label: string;
  onClick?: () => void;
};

// Бургер из хранилища компонентов (`burger`, исходно clos-front): линии толще и квадратная
// кнопка 44 px под палец. Своё состояние не держит — open/closed задаёт шапка
export const Burger: FCClass<TBurger> = ({
  className,
  state = 'closed',
  label,
  onClick,
}) => {
  return (
    <button
      type="button"
      className={cx(styles.burger, state === 'open' && styles.burger_open, 'focus-ring', className)}
      aria-label={label}
      aria-expanded={state === 'open'}
      onClick={onClick}
    >
      <span className={styles.lines}>
        <span />
      </span>
    </button>
  );
};
