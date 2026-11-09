import { Reveal } from '@/shared/ui/reveal';
import { TStubComponent } from '../model/schema';

// Заглушка страницы, пока нет настоящих блоков (задача сайта 03)
export const StubComponent: FCClass<TStubComponent> = ({
  className,
  title,
  description,
}) => {
  return (
    <section className={className}>
      <Reveal type="fadeUp" className="container flex flex-col gap-24 py-80 t:py-120">
        {title && (
          <h1 className="t-h1">{title}</h1>
        )}

        {description && (
          <p className="t-body-l max-w-640 text-muted">{description}</p>
        )}
      </Reveal>
    </section>
  );
};
