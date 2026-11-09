// Пиксели макета → rem, как rem() в SCSS: 16 px = 1rem. Для размеров, которые задаются из кода
export const rem = (px: number) => `${px / 16}rem`;
