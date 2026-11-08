import {
  addScrollableSelector,
  clearQueueScrollLocks,
  disablePageScroll,
  enablePageScroll,
} from 'scroll-lock';

/**
 *  Класс с методами для отключения скролла на странице.
 *  При отключении скролла справа добавляется падинг на ширину скроллбара, если он нужен.
 *  При отключении скролла надо элементу, который скролится,
 *  проставить аттрибут data-scroll-lock-scrollable
 *  Чтобы к элементу добавлялся паддинг размером с скроллбар
 *  надо проставить аттрибут data-scroll-lock-fill-gap
 */
export const useScrollLock = () => {
  const lock = (element?: HTMLElement) => {
    addScrollableSelector(['[data-overlayscrollbars-viewport]', '[data-scroll-lock-scrollable]']);
    disablePageScroll(element);
  };

  const enable = (element?: HTMLElement) => {
    enablePageScroll(element);
  };

  const enableAll = () => {
    clearQueueScrollLocks();
    enablePageScroll();
  };

  return { lock, enable, enableAll };
};
