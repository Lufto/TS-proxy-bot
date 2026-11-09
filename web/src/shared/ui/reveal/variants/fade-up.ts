import { TRevealVariantObject } from '../types';

export const fadeUpVariant: TRevealVariantObject = {
  initial: {
    opacity: 0,
    y: '3vh',
  },
  inView: {
    opacity: 1,
    y: '0vh',
  },
};
