import type { TKitVariant, TKitVariantId } from './variants';

// Знаки вариантов на сетке 32×32 (наброски, задача сайта 05). Из одной разметки
// собираются и логотип на странице кита, и фавикон вкладки, поэтому только фигуры, без шрифтов.
const MARKS: Record<TKitVariantId, (accent: string, onAccent: string) => string> = {
  // «Сигнал»: буква T на лаймовой плашке и точка-сигнал
  a: (accent, onAccent) => `
    <rect width="32" height="32" rx="7" fill="${accent}"/>
    <rect x="6" y="8" width="16" height="4" fill="${onAccent}"/>
    <rect x="12" y="12" width="4" height="13" fill="${onAccent}"/>
    <rect x="21" y="20" width="5" height="5" fill="${onAccent}"/>`,
  // «Сейф»: кольцо и замочная скважина
  b: (accent, onAccent) => `
    <circle cx="16" cy="16" r="16" fill="${accent}"/>
    <circle cx="16" cy="16" r="10" fill="none" stroke="${onAccent}" stroke-width="2.5"/>
    <circle cx="16" cy="13.5" r="2.6" fill="${onAccent}"/>
    <rect x="14.75" y="14" width="2.5" height="7" rx="1.25" fill="${onAccent}"/>`,
  // «Манифест»: рубленые TS на оранжевом квадрате
  c: (accent, onAccent) => `
    <rect width="32" height="32" fill="${accent}"/>
    <rect x="4" y="7" width="11" height="4" fill="${onAccent}"/>
    <rect x="7.5" y="7" width="4" height="18" fill="${onAccent}"/>
    <rect x="17" y="7" width="11" height="4" fill="${onAccent}"/>
    <rect x="17" y="7" width="4" height="10" fill="${onAccent}"/>
    <rect x="17" y="14" width="11" height="4" fill="${onAccent}"/>
    <rect x="24" y="14" width="4" height="11" fill="${onAccent}"/>
    <rect x="17" y="21" width="11" height="4" fill="${onAccent}"/>`,
};

const color = (variant: TKitVariant, name: string) =>
  variant.palette.find(swatch => swatch.name === name)?.hex ?? '#000000';

export const markSvg = (variant: TKitVariant, inverted = false) => {
  const accent = color(variant, 'accent');
  const onAccent = color(variant, 'on-accent');
  const body = inverted ? MARKS[variant.id](onAccent, accent) : MARKS[variant.id](accent, onAccent);
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">${body}</svg>`;
};

export const faviconDataUri = (variant: TKitVariant) =>
  `data:image/svg+xml,${encodeURIComponent(markSvg(variant))}`;
