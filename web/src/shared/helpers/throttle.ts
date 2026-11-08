export type TthrottledEventHandler = (this: any) => void;
export type Tthrottle = (callback: (...args: any[]) => any, delay?: number) => TthrottledEventHandler;

/**
 * функция, которая вызывает другую функцию,
 * «пропуская» некоторые вызовы с определённой периодичностью.
 */
export const throttle: Tthrottle = (fn, wait = 300) => {
  let inThrottle: boolean;
  let lastFn: ReturnType<typeof setTimeout>;
  let lastTime: number;

  return function throttlefn(this: any, ...args) {
    if (!inThrottle) {
      fn.apply(this, args);
      lastTime = Date.now();
      inThrottle = true;
    }
    else {
      clearTimeout(lastFn);
      lastFn = setTimeout(() => {
        if (Date.now() - lastTime >= wait) {
          fn.apply(this, args);
          lastTime = Date.now();
        }
      }, Math.max(wait - (Date.now() - lastTime), 0));
    }
  };
};
