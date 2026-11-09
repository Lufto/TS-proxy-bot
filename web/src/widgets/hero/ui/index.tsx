import { resolveUrl } from '@/shared/lib/resolve-url';
import { Button } from '@/shared/ui/button';
import { Reveal, RevealItem } from '@/shared/ui/reveal';
import { THero } from '../model/schema';

// Первый экран главной: крупный заголовок, две кнопки и короткие факты
export const Hero: FCClass<THero> = ({
  label, title, text, primary, secondary, facts,
}) => (
  <section className="container">
    <div className="flex flex-col gap-48 pt-56 pb-56 t:pt-96 t:pb-80">
      <Reveal type="fadeUp" className="flex max-w-960 flex-col gap-24">
        {label && <span className="t-label text-accent">{label}</span>}
        <h1 className="t-display">{title}</h1>
        {text && <p className="t-body-l max-w-640 text-muted">{text}</p>}
        <div className="flex flex-wrap gap-12 pt-8">
          <Button url={resolveUrl(primary.url)}>{primary.title}</Button>
          {secondary && <Button variant="secondary" url={resolveUrl(secondary.url)}>{secondary.title}</Button>}
        </div>
      </Reveal>
      {facts && facts.length > 0 && (
        <Reveal type="fadeUp" tag="ul" staggerChildren={0.1} className="rule grid gap-24 pt-32 t:grid-cols-3">
          {facts.map(fact => (
            <RevealItem key={fact.value} tag="li" className="flex flex-col gap-4">
              <span className="t-h3">{fact.value}</span>
              <span className="t-caption">{fact.label}</span>
            </RevealItem>
          ))}
        </Reveal>
      )}
    </div>
  </section>
);
