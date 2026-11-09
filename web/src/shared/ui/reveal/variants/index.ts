import { TRevealVariantObject, TRevealType } from '../types';
import { fadeVariant } from './fade';
import { fadeUpVariant } from './fade-up';
import { scaleVariant } from './scale';

export const variants: Record<TRevealType, TRevealVariantObject> = {
  fade: fadeVariant,
  fadeUp: fadeUpVariant,
  scale: scaleVariant,
};
