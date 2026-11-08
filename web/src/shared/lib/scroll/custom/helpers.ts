import type { OverlayScrollbars } from 'overlayscrollbars';

export function centerCustomScroll(inst: OverlayScrollbars, progress = 0.5): void {
  const { content } = inst.elements();

  content.scrollTo({
    left: (content.scrollWidth - content.offsetWidth) * progress,
  });
}
