export type TButtonVariant = 'primary' | 'secondary' | 'ghost';

export type TButtonSize = 'l' | 'm';

export type TButton = {
  variant?: TButtonVariant;
  size?: TButtonSize;
  /** Адрес ссылки; без него рендерится кнопка */
  url?: string;
  target?: React.AnchorHTMLAttributes<HTMLAnchorElement>['target'];
  /** Стрелка после надписи; по умолчанию есть только у главной кнопки */
  arrow?: boolean;
  disabled?: boolean;
  submit?: boolean;
  onClick?: React.MouseEventHandler<HTMLElement>;
};
