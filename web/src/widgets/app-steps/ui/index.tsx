import { SITE_CONFIG } from '@/generated-config';
import { Button } from '@/shared/ui/button';
import { Reveal } from '@/shared/ui/reveal';
import { RichText } from '@/shared/ui/rich-text';
import { TAppSteps } from '../model/schema';

type TApps = Record<string, Record<string, string> | undefined>;

// Инструкция для одного устройства: ссылки на приложение и шаги с номерами
export const AppSteps: FCClass<TAppSteps> = ({
  label, title, text, downloads, steps,
}) => {
  const apps = SITE_CONFIG.apps as TApps;
  const links = downloads
    .map(download => ({ ...download, url: apps[download.app]?.[download.store] }))
    .filter(download => download.url);

  return (
    <section className="container">
      <div className="flex flex-col gap-40 pt-56 pb-56 t:pt-96 t:pb-80">
        <Reveal type="fadeUp" className="flex max-w-820 flex-col gap-20">
          {label && <span className="t-label text-accent">{label}</span>}
          <h1 className="t-h1">{title}</h1>
          {text && <p className="t-body-l text-muted"><RichText text={text} /></p>}
          {links.length > 0 && (
            <div className="flex flex-wrap gap-12 pt-8">
              {links.map((link, index) => (
                <Button
                  key={`${link.app}-${link.store}`}
                  size="m"
                  variant={index === 0 ? 'primary' : 'secondary'}
                  url={link.url}
                >
                  {link.title}
                </Button>
              ))}
            </div>
          )}
        </Reveal>

        <ol className="flex max-w-820 flex-col">
          {steps.map((step, index) => (
            <Step key={step.title} index={index} {...step} />
          ))}
        </ol>
      </div>
    </section>
  );
};

const Step: FCClass<TAppSteps['steps'][number] & { index: number }> = ({
  index, title, text, note, image,
}) => (
  <Reveal type="fadeUp" tag="li" className="rule grid gap-16 py-32 t:grid-cols-[80px_1fr]">
    <span className="font-display text-[40px] leading-none font-extrabold text-accent">{index + 1}</span>
    <div className="flex flex-col gap-12">
      <h2 className="t-h3">{title}</h2>
      <p className="t-body-l text-cream/85"><RichText text={text} /></p>
      {note && <p className="t-body border-l-2 border-accent pl-16 text-muted"><RichText text={note} /></p>}
      {image && (
        <div className="mt-8 w-fit border border-line bg-surface p-8">
          {/* Скриншоты сжаты заранее и с настоящими размерами — оптимизатор next/image тут не нужен */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={image.src} width={image.width} height={image.height} alt={image.alt} loading="lazy" className="h-auto max-h-560 w-auto" />
        </div>
      )}
    </div>
  </Reveal>
);
