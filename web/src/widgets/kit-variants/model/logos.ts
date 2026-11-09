import type { TKitVariant, TKitVariantId } from './variants';

type TMark = (accent: string, onAccent: string) => string;

// Как пишется название рядом со знаком
export type TWordmarkStyle = 'slash' | 'plain' | 'plate' | 'brackets' | 'arrow';

export type TLogoOption = {
  id: string;
  name: string;
  idea: string;
  wordmark: TWordmarkStyle;
  mark: TMark;
};

// Знаки на сетке 32×32 (задача сайта 05). Из одной разметки собираются и логотип
// на странице кита, и фавикон вкладки, поэтому только фигуры, без шрифтов.
const MARKS: Record<Exclude<TKitVariantId, 'c'>, TMark> = {
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
};

// Варианты знака для «Манифеста»: владелец выбрал этот стиль и просил переделать логотип.
// Все — прямые углы, оранжевый и чёрный, как плакат.
export const MANIFEST_LOGOS: TLogoOption[] = [
  {
    id: '1',
    name: 'Слэш',
    idea: 'Косая черта из названия TS/PROXY становится знаком: трафик идёт насквозь, напрямую.',
    wordmark: 'slash',
    mark: (accent, onAccent) => `
      <rect width="32" height="32" fill="${accent}"/>
      <polygon points="5,27 14,27 27,5 18,5" fill="${onAccent}"/>`,
  },
  {
    id: '2',
    name: 'Трафарет',
    idea: 'Рубленые TS, рассечённые двумя прорезями, как трафарет уличного плаката. Прорези — намёк на шифр.',
    wordmark: 'plain',
    mark: (accent, onAccent) => `
      <rect width="32" height="32" fill="${accent}"/>
      <rect x="4" y="7" width="11" height="4" fill="${onAccent}"/>
      <rect x="7.5" y="7" width="4" height="18" fill="${onAccent}"/>
      <rect x="17" y="7" width="11" height="4" fill="${onAccent}"/>
      <rect x="17" y="7" width="4" height="10" fill="${onAccent}"/>
      <rect x="17" y="14" width="11" height="4" fill="${onAccent}"/>
      <rect x="24" y="14" width="4" height="11" fill="${onAccent}"/>
      <rect x="17" y="21" width="11" height="4" fill="${onAccent}"/>
      <rect y="11.75" width="32" height="1.5" fill="${accent}"/>
      <rect y="18.75" width="32" height="1.5" fill="${accent}"/>`,
  },
  {
    id: '3',
    name: 'Цензура',
    idea: 'Строки, закрытые чёрными плашками, как в рассекреченном документе: ваш трафик никто не прочтёт.',
    wordmark: 'plate',
    mark: (accent, onAccent) => `
      <rect width="32" height="32" fill="${accent}"/>
      <rect x="5" y="7" width="22" height="5" fill="${onAccent}"/>
      <rect x="5" y="13.5" width="13" height="5" fill="${onAccent}"/>
      <rect x="5" y="20" width="18" height="5" fill="${onAccent}"/>`,
  },
  {
    id: '4',
    name: 'Скобки',
    idea: 'Квадратные скобки вокруг точки — ваше соединение в защищённой оболочке.',
    wordmark: 'brackets',
    mark: (accent, onAccent) => `
      <rect width="32" height="32" fill="${accent}"/>
      <rect x="5" y="6" width="4" height="20" fill="${onAccent}"/>
      <rect x="5" y="6" width="8" height="4" fill="${onAccent}"/>
      <rect x="5" y="22" width="8" height="4" fill="${onAccent}"/>
      <rect x="23" y="6" width="4" height="20" fill="${onAccent}"/>
      <rect x="19" y="6" width="8" height="4" fill="${onAccent}"/>
      <rect x="19" y="22" width="8" height="4" fill="${onAccent}"/>
      <rect x="13" y="13" width="6" height="6" fill="${onAccent}"/>`,
  },
  {
    id: '5',
    name: 'Проход',
    idea: 'Тяжёлый шеврон вперёд: прокси пропускает туда, куда напрямую не пускают.',
    wordmark: 'arrow',
    mark: (accent, onAccent) => `
      <rect width="32" height="32" fill="${accent}"/>
      <polygon points="7,5 15,5 26,16 15,27 7,27 18,16" fill="${onAccent}"/>`,
  },
  {
    id: '6',
    name: 'Сдвиг',
    idea: 'Буква T на чёрном квадрате с оранжевой «тенью-сдвигом» — та же форма, что у кнопок кита при наведении.',
    wordmark: 'plain',
    mark: (accent, onAccent) => `
      <rect x="9" y="9" width="23" height="23" fill="${accent}"/>
      <rect x="1" y="1" width="22" height="22" fill="${onAccent}" stroke="${accent}" stroke-width="2"/>
      <rect x="5" y="6" width="14" height="4" fill="${accent}"/>
      <rect x="10" y="10" width="4" height="9" fill="${accent}"/>`,
  },
];

// Выбор владельца на 2026-11-09 (пока не окончательный) — «Сдвиг»
export const DEFAULT_LOGO_ID = '6';

export const findLogoOption = (logoId: string) =>
  MANIFEST_LOGOS.find(option => option.id === logoId)
  ?? MANIFEST_LOGOS.find(option => option.id === DEFAULT_LOGO_ID)!;

const color = (variant: TKitVariant, name: string) =>
  variant.palette.find(swatch => swatch.name === name)?.hex ?? '#000000';

export const markSvg = (variant: TKitVariant, logoId = DEFAULT_LOGO_ID, inverted = false) => {
  const accent = color(variant, 'accent');
  const onAccent = color(variant, 'on-accent');
  const mark = variant.id === 'c' ? findLogoOption(logoId).mark : MARKS[variant.id];
  const body = inverted ? mark(onAccent, accent) : mark(accent, onAccent);
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">${body}</svg>`;
};

export const faviconDataUri = (variant: TKitVariant, logoId?: string) =>
  `data:image/svg+xml,${encodeURIComponent(markSvg(variant, logoId))}`;
