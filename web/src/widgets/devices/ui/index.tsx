import { Link as NextLink } from '@/shared/lib/i18n/navigation';
import { Reveal, RevealItem } from '@/shared/ui/reveal';
import { Section } from '@/shared/ui/section';
import { TDevices } from '../model/schema';

// Устройства — карточки-ссылки на инструкции
export const Devices: FCClass<TDevices> = ({
  id, label, title, text, items,
}) => (
  <Section id={id} label={label} title={title} text={text}>
    <Reveal type="fadeUp" tag="ul" staggerChildren={0.08} className="grid gap-12 t:grid-cols-2">
      {items.map(item => (
        <RevealItem key={item.url} tag="li">
          <NextLink
            href={item.url}
            className="group focus-ring flex h-full items-center justify-between gap-16 border border-line bg-surface p-24 transition-colors duration-200 hover:border-accent"
          >
            <span className="flex flex-col gap-6">
              <span className="t-h3">{item.title}</span>
              <span className="t-caption">{item.text}</span>
            </span>
            <span className="t-h3 text-accent transition-transform duration-200 group-hover:translate-x-6" aria-hidden>→</span>
          </NextLink>
        </RevealItem>
      ))}
    </Reveal>
  </Section>
);
