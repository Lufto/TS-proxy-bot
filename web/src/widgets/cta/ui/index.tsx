import { resolveUrl } from '@/shared/lib/resolve-url';
import { Button } from '@/shared/ui/button';
import { Reveal } from '@/shared/ui/reveal';
import { TCta } from '../model/schema';

// Финальный призыв: карточка с оранжевой рамкой; первая кнопка главная, остальные — вторые
export const Cta: FCClass<TCta> = ({ title, text, buttons }) => (
  <section className="container py-56 t:py-80">
    <Reveal type="fadeUp" className="flex flex-col gap-24 border-2 border-accent bg-surface px-20 py-40 t:px-48 t:py-64">
      <h2 className="t-h1 max-w-820">{title}</h2>
      {text && <p className="t-body-l max-w-640 text-muted">{text}</p>}
      <div className="flex flex-wrap gap-12 pt-8">
        {buttons.map((button, index) => (
          <Button
            key={button.title}
            variant={button.variant ?? (index === 0 ? 'primary' : 'secondary')}
            url={resolveUrl(button.url)}
          >
            {button.title}
          </Button>
        ))}
      </div>
    </Reveal>
  </section>
);
