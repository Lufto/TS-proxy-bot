// Шрифты вариантов кита (задача сайта 05); у всех есть кириллица.
// Черновая страница грузит их ссылкой на Google Fonts в браузере, а не через next/font:
// next/font/google то и дело ронял сборку на скачивании. Шрифты «Манифеста» лежат в проекте
const family = (name: string, fallback = 'sans-serif') => ({ style: { fontFamily: `'${name}', ${fallback}` } });

export const geologica = family('Geologica');
export const onest = family('Onest');
export const jetbrainsMono = family('JetBrains Mono', 'monospace');
export const manrope = family('Manrope');
export const unbounded = family('Unbounded');
export const golosText = family('Golos Text');

export const GOOGLE_FONTS_HREF = 'https://fonts.googleapis.com/css2?family=Geologica:wght@500;700'
  + '&family=Onest:wght@400;500;600&family=Manrope:wght@400;500;600;700&display=swap';
