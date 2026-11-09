import { Reveal, RevealItem } from '@/shared/ui/reveal';
import { RichText } from '@/shared/ui/rich-text';
import { Section } from '@/shared/ui/section';
import { TSteps } from '../model/schema';

// Шаги по порядку с крупными оранжевыми номерами
export const Steps: FCClass<TSteps> = ({
  id, label, title, text, items,
}) => (
  <Section id={id} label={label} title={title} text={text}>
    <Reveal type="fadeUp" tag="ul" staggerChildren={0.1} className="grid gap-32 md:grid-cols-3 md:gap-24">
      {items.map((item, index) => (
        <RevealItem key={item.title} tag="li" className="flex flex-col gap-12 border-t-2 border-accent pt-20">
          <span className="font-display text-[48px] font-extrabold leading-none text-accent">{index + 1}</span>
          <h3 className="t-h3">{item.title}</h3>
          <p className="t-body text-muted"><RichText text={item.text} /></p>
        </RevealItem>
      ))}
    </Reveal>
  </Section>
);
