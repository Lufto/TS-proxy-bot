'use client';

import { useRef, useState, useEffect } from 'react';
import { RevealProvider } from '../context';
import { TRevealVariantObject, TReveal, TRevealState } from '../types';
import { variants } from '../variants';
import { DEF_ANIMATION_DURATION } from '../const';
import { motionComponents } from '../motion-components';

type TGetStaggerPropsParams = {
  variantTypeObject: TRevealVariantObject;
  staggerChildren: TReveal['staggerChildren'];
};

const getVariantsProps = (params: TGetStaggerPropsParams) => {
  const { staggerChildren, variantTypeObject } = params;

  if (typeof staggerChildren === 'number') {
    return {
      initial: {
        opacity: 0,
      },
      inView: {
        opacity: 1,
        transition: {
          staggerChildren,
        },
      },
    };
  }

  return variantTypeObject;
};

export const Reveal: FCClass<TReveal> = ({
  type = 'fadeUp',
  tag = 'div',
  staggerChildren,
  className,
  children,
}) => {
  const MotionTag = motionComponents[tag];

  const elementRef = useRef<HTMLElement>(null);
  const [animate, setAnimate] = useState<TRevealState | 'remove'>('initial');

  useEffect(() => {
    const element = elementRef.current;

    let observer: IntersectionObserver;

    if (element) {
      const {
        top, left, right, bottom,
      } = element.getBoundingClientRect();

      const inViewport = top < window.innerHeight
        && bottom > 0
        && left < window.innerWidth
        && right > 0;

      const getStrategyKey = () => {
        if (inViewport) {
          return 'inView';
        }

        if (top < 0) {
          return 'remove';
        }

        return 'watch';
      };

      const key = getStrategyKey();
      switch (key) {
        case 'watch':
          observer = new IntersectionObserver(
            ([entry]) => {
              if (entry.isIntersecting) {
                setAnimate('inView');

                observer.disconnect();
              }
            },
            {
              threshold: 0.1,
            },
          );

          observer.observe(element);

          break;

        case 'inView':
          requestAnimationFrame(() => {
            setAnimate('inView');
          });

          break;

        case 'remove':
        default:
          setAnimate('remove');

          break;
      }
    }

    return () => {
      observer?.disconnect();
    };
  }, []);

  const variantTypeObject = variants[type];

  return (
    <RevealProvider
      type={type}
    >
      <MotionTag
        ref={elementRef as React.Ref<any>}
        className={className}
        variants={{
          ...getVariantsProps({ staggerChildren, variantTypeObject }),
          remove: {
            ...variantTypeObject.inView,
            transition: {
              duration: 0,
            },
          },
        }}
        initial="initial"
        animate={animate}
        transition={{
          duration: DEF_ANIMATION_DURATION,
        }}
      >
        {children}
      </MotionTag>
    </RevealProvider>
  );
};
