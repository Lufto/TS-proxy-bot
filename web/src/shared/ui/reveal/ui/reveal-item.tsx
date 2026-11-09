'use client';

import { useRevealProvider } from '../context';
import { variants } from '../variants';
import { DEF_ANIMATION_DURATION } from '../const';
import { motionComponents } from '../motion-components';
import { TRevealItem } from '../types';

export const RevealItem: FCClass<TRevealItem> = ({
  className,
  children,
  tag = 'div',
}) => {
  const MotionTag = motionComponents[tag];

  const { type } = useRevealProvider();

  const variantTypeObject = variants[type];

  return (
    <MotionTag
      className={className}
      variants={{
        ...variantTypeObject,
        remove: {
          ...variantTypeObject.inView,
          transition: {
            duration: 0,
          },
        },
      }}
      transition={{
        duration: DEF_ANIMATION_DURATION,
      }}
    >
      {children}
    </MotionTag>
  );
};
