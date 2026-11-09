export const enum LinkUnderline {
  /** Линия выезжает при наведении */
  hover = 'hover',
  /** Постоянная оранжевая линия — ссылка внутри текста */
  solid = 'solid',
  none = 'none',
}

export const enum LinkColor {
  cream = 'cream',
  muted = 'muted',
  accent = 'accent',
}

export type TLink = {
  underline?: LinkUnderline;
  color?: LinkColor;
  /** Стрелка после текста, сдвигается при наведении */
  arrow?: boolean;
  typography?: string;
  url?: string;
  target?: React.AnchorHTMLAttributes<HTMLAnchorElement>['target'];
  disabled?: boolean;
  submit?: boolean;
  rel?: string;
  onClick?: React.MouseEventHandler<HTMLElement>;
};
