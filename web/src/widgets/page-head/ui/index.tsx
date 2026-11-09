import { Reveal } from '@/shared/ui/reveal';
import { RichText } from '@/shared/ui/rich-text';
import { TPageHead } from '../model/schema';

// Заголовок внутренней страницы
export const PageHead: FCClass<TPageHead> = ({ label, title, text }) => (
  <section className="container">
    <Reveal type="fadeUp" className="flex max-w-820 flex-col gap-20 pt-56 pb-8 t:pt-96">
      {label && <span className="t-label text-accent">{label}</span>}
      <h1 className="t-h1">{title}</h1>
      {text && <p className="t-body-l text-muted"><RichText text={text} /></p>}
    </Reveal>
  </section>
);
