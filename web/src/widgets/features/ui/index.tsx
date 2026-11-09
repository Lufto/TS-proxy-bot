import { Reveal, RevealItem } from '@/shared/ui/reveal';
import { RichText } from '@/shared/ui/rich-text';
import { Section } from '@/shared/ui/section';
import { TFeatures } from '../model/schema';

// Карточки «что входит»: номер, заголовок, пара строк текста
export const Features: FCClass<TFeatures> = ({
  id, label, title, text, items,
}) => (
  <Section id={id} label={label} title={title} text={text}>
    <Reveal type="fadeUp" tag="ul" staggerChildren={0.08} className="grid gap-12 t:grid-cols-2 md:grid-cols-3">
      {items.map((item, index) => (
        <RevealItem key={item.title} tag="li" className="flex flex-col gap-12 border border-line bg-surface p-24">
          <span className="t-label text-accent">{String(index + 1).padStart(2, '0')}</span>
          <h3 className="t-h3">{item.title}</h3>
          <p className="t-body text-muted"><RichText text={item.text} /></p>
        </RevealItem>
      ))}
    </Reveal>
  </Section>
);
