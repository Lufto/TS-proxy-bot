import type { Variant } from 'motion';
import { motionComponents } from '../motion-components';

export type TRevealState = 'initial' | 'inView';

export type TRevealVariantObject = Record<TRevealState, Variant>;

export type TRevealType = 'fade' | 'fadeUp' | 'scale';

export type TRevealSupportedTag = keyof typeof motionComponents;

export type TReveal = {
  type?: TRevealType;
  tag?: TRevealSupportedTag;
  staggerChildren?: number;
};

export type TRevealItem = {
  tag?: TRevealSupportedTag;
};
