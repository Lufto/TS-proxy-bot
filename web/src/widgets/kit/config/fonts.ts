import {
  Geologica, Golos_Text, JetBrains_Mono, Manrope, Onest, Unbounded,
} from 'next/font/google';

// Шрифты вариантов кита (задача сайта 05); у всех есть кириллица
export const geologica = Geologica({ subsets: ['latin', 'cyrillic'], weight: ['500', '700'] });
export const onest = Onest({ subsets: ['latin', 'cyrillic'], weight: ['400', '500', '600'] });
export const jetbrainsMono = JetBrains_Mono({ subsets: ['latin', 'cyrillic'], weight: ['400', '500'] });
export const manrope = Manrope({ subsets: ['latin', 'cyrillic'], weight: ['400', '500', '600', '700'] });
export const unbounded = Unbounded({ subsets: ['latin', 'cyrillic'], weight: ['500', '800'] });
export const golosText = Golos_Text({ subsets: ['latin', 'cyrillic'], weight: ['400', '500', '600'] });
