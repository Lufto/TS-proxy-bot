import cx from 'classix';
import { Reveal } from '@/shared/ui/reveal';

type TSection = {
  id?: string;
  label?: string;
  title?: string;
  text?: string;
  /** Линия сверху — разделитель между блоками */
  rule?: boolean;
};

// Обёртка блока страницы: отступы, метка, заголовок и подзаголовок в стиле кита
export const Section: FCClass<TSection> = ({
  id, label, title, text, rule = true, className, children,
}) => (
  <section id={id} className={cx('container scroll-mt-80', className)}>
    <div className={cx('flex flex-col gap-32 py-56 t:gap-40 t:py-80', rule && 'rule')}>
      {(label || title || text) && (
        <Reveal type="fadeUp" className="flex max-w-820 flex-col gap-16">
          {label && <span className="t-label text-accent">{label}</span>}
          {title && <h2 className="t-h2">{title}</h2>}
          {text && <p className="t-body-l text-muted">{text}</p>}
        </Reveal>
      )}
      {children}
    </div>
  </section>
);
