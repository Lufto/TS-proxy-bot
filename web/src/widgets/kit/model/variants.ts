import type { CSSProperties } from 'react';
import {
  geologica, golosText, jetbrainsMono, manrope, onest, unbounded,
} from '../config/fonts';

export const KIT_VARIANT_IDS = ['a', 'b', 'c'] as const;
export type TKitVariantId = typeof KIT_VARIANT_IDS[number];

export type TKitSwatch = {
  name: string;
  role: string;
  hex: string;
};

export type TKitVariant = {
  id: TKitVariantId;
  name: string;
  idea: string;
  fonts: {
    display: string;
    body: string;
    mono: string;
  };
  palette: TKitSwatch[];
  // CSS-переменные варианта: цвета, шрифты, скругления, характер заголовков
  tokens: CSSProperties;
};

const swatchTokens = (palette: TKitSwatch[]) => Object.fromEntries(
  palette.map(swatch => [`--kit-${swatch.name}`, swatch.hex]),
);

const variant = (
  data: Omit<TKitVariant, 'tokens'>,
  fonts: { display: string; body: string; mono: string },
  tokens: Record<string, string>,
): TKitVariant => ({
  ...data,
  tokens: {
    ...swatchTokens(data.palette),
    '--kit-font-display': fonts.display,
    '--kit-font-body': fonts.body,
    '--kit-font-mono': fonts.mono,
    ...tokens,
  } as CSSProperties,
});

export const KIT_VARIANTS: Record<TKitVariantId, TKitVariant> = {
  a: variant(
    {
      id: 'a',
      name: 'Сигнал',
      idea: 'Строгий технологичный: графит, лаймовый сигнал, моноширинные подписи и тонкая сетка. Похоже на инструмент инженера.',
      fonts: { display: 'Geologica', body: 'Onest', mono: 'JetBrains Mono' },
      palette: [
        { name: 'bg', role: 'Фон', hex: '#0A0C0F' },
        { name: 'surface', role: 'Карточки', hex: '#12161B' },
        { name: 'border', role: 'Линии', hex: '#242B34' },
        { name: 'text', role: 'Текст', hex: '#E8ECF1' },
        { name: 'muted', role: 'Второстепенный текст', hex: '#8A94A3' },
        { name: 'accent', role: 'Акцент', hex: '#C8FF4D' },
        { name: 'on-accent', role: 'Текст на акценте', hex: '#0A0C0F' },
      ],
    },
    {
      display: geologica.style.fontFamily,
      body: onest.style.fontFamily,
      mono: jetbrainsMono.style.fontFamily,
    },
    {
      '--kit-radius': '6px',
      '--kit-radius-button': '6px',
      '--kit-display-weight': '700',
      '--kit-display-tracking': '-0.035em',
      '--kit-display-transform': 'none',
      '--kit-display-leading': '1.02',
      '--kit-backdrop': 'linear-gradient(var(--kit-border) 1px, transparent 1px) 0 0 / 48px 48px, linear-gradient(90deg, var(--kit-border) 1px, transparent 1px) 0 0 / 48px 48px',
      '--kit-backdrop-opacity': '0.35',
      '--kit-glow': 'radial-gradient(60% 60% at 80% 0%, rgba(200, 255, 77, 0.16), transparent 70%)',
    },
  ),
  b: variant(
    {
      id: 'b',
      name: 'Сейф',
      idea: 'Спокойный, как хорошее банковское приложение: глубокий синий, мятный акцент, мягкие формы и много воздуха. Внушает доверие.',
      fonts: { display: 'Manrope', body: 'Manrope', mono: 'JetBrains Mono' },
      palette: [
        { name: 'bg', role: 'Фон', hex: '#070B14' },
        { name: 'surface', role: 'Карточки', hex: '#0E1524' },
        { name: 'border', role: 'Линии', hex: '#1D2842' },
        { name: 'text', role: 'Текст', hex: '#E6EBF5' },
        { name: 'muted', role: 'Второстепенный текст', hex: '#8D99B2' },
        { name: 'accent', role: 'Акцент', hex: '#6EE7C8' },
        { name: 'on-accent', role: 'Текст на акценте', hex: '#04130F' },
      ],
    },
    {
      display: manrope.style.fontFamily,
      body: manrope.style.fontFamily,
      mono: jetbrainsMono.style.fontFamily,
    },
    {
      '--kit-radius': '20px',
      '--kit-radius-button': '999px',
      '--kit-display-weight': '600',
      '--kit-display-tracking': '-0.03em',
      '--kit-display-transform': 'none',
      '--kit-display-leading': '1.05',
      '--kit-backdrop': 'none',
      '--kit-backdrop-opacity': '0',
      '--kit-glow': 'radial-gradient(55% 70% at 15% 0%, rgba(84, 120, 255, 0.22), transparent 70%), radial-gradient(45% 60% at 95% 20%, rgba(110, 231, 200, 0.14), transparent 70%)',
    },
  ),
  c: variant(
    {
      id: 'c',
      name: 'Манифест',
      idea: 'Смелая типографика: огромные заголовки, тёплый белый на чёрном, оранжевый сигнальный цвет и прямые углы. Похоже на плакат.',
      fonts: { display: 'Unbounded', body: 'Golos Text', mono: 'JetBrains Mono' },
      palette: [
        { name: 'bg', role: 'Фон', hex: '#0B0B0B' },
        { name: 'surface', role: 'Карточки', hex: '#161616' },
        { name: 'border', role: 'Линии', hex: '#2B2B2B' },
        { name: 'text', role: 'Текст', hex: '#F3EFE6' },
        { name: 'muted', role: 'Второстепенный текст', hex: '#9D978B' },
        { name: 'accent', role: 'Акцент', hex: '#FF5B22' },
        { name: 'on-accent', role: 'Текст на акценте', hex: '#0B0B0B' },
      ],
    },
    {
      display: unbounded.style.fontFamily,
      body: golosText.style.fontFamily,
      mono: jetbrainsMono.style.fontFamily,
    },
    {
      '--kit-radius': '0px',
      '--kit-radius-button': '0px',
      '--kit-display-weight': '800',
      '--kit-display-tracking': '-0.02em',
      '--kit-display-transform': 'uppercase',
      '--kit-display-leading': '0.98',
      '--kit-backdrop': 'none',
      '--kit-backdrop-opacity': '0',
      '--kit-glow': 'radial-gradient(40% 50% at 100% 100%, rgba(255, 91, 34, 0.18), transparent 70%)',
    },
  ),
};

export const isKitVariantId = (value: unknown): value is TKitVariantId =>
  typeof value === 'string' && (KIT_VARIANT_IDS as readonly string[]).includes(value);
