import { RefObject, useEffect, useState } from 'react';

/** Хук, который следит за рефом, если он виден на экране, то возращается true */
export function useOnScreen<T = HTMLElement | null>(ref: RefObject<T>, threshold = 0.5) {
  const [isIntersecting, setIsIntersecting] = useState(false);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        setIsIntersecting(entry.isIntersecting);
      },
      { threshold },
    );

    if (ref.current instanceof Element) {
      observer.observe(ref.current);
    }

    return () => {
      observer.disconnect();
    };
  }, [ref]);

  return { isIntersecting };
}
