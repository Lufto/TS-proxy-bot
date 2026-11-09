'use client';

import cx from 'classix';
import { useState } from 'react';
import { SITE_CONFIG } from '@/generated-config';
import { Button } from '@/shared/ui/button';
import { Reveal } from '@/shared/ui/reveal';
import { RichText } from '@/shared/ui/rich-text';
import { TAppSteps, TAppStepsStep } from '../model/schema';

type TApps = Record<string, Record<string, string> | undefined>;

// Инструкция для одного устройства: выбор приложения карточками, ссылки на него и шаги с номерами
export const AppSteps: FCClass<TAppSteps> = ({
  label, title, text, apps,
}) => {
  const [current, setCurrent] = useState(0);
  const config = SITE_CONFIG.apps as TApps;
  const app = apps[current] ?? apps[0];
  const name = (key: string) => config[key]?.name ?? key;
  const links = app.downloads
    .map(download => ({ ...download, url: config[app.app]?.[download.store] }))
    .filter(download => download.url);

  return (
    <section className="container">
      <div className="flex flex-col gap-40 pt-56 pb-56 t:pt-96 t:pb-80">
        <Reveal type="fadeUp" className="flex max-w-820 flex-col gap-20">
          {label && <span className="t-label text-accent">{label}</span>}
          <h1 className="t-h1">{title}</h1>
          {text && <p className="t-body-l text-muted"><RichText text={text} /></p>}
        </Reveal>

        {apps.length > 1 && (
          <div className="grid max-w-820 grid-cols-2 gap-8 t:grid-cols-4">
            {apps.map((item, index) => (
              <button
                key={item.app}
                type="button"
                aria-pressed={index === current}
                onClick={() => setCurrent(index)}
                className={cx(
                  'relative flex cursor-pointer flex-col items-start gap-6 border bg-surface p-16 text-left transition-colors',
                  index === current ? 'border-accent bg-accent/10 shadow-[inset_0_0_0_1px_var(--color-accent)]' : 'border-line hover:border-muted',
                )}
              >
                <span className={cx('t-label', index === 0 ? 'text-accent' : 'text-muted')}>{item.tag}</span>
                <span className="font-display text-[1.25rem] leading-tight font-bold">{name(item.app)}</span>
                {item.hint && <span className="text-[0.8125rem] leading-snug text-muted">{item.hint}</span>}
                {/* Отметка выбранного — квадрат-«сдвиг» в углу, как знак кита */}
                {index === current && <span aria-hidden="true" className="absolute top-12 right-12 size-10 bg-accent" />}
              </button>
            ))}
          </div>
        )}

        {links.length > 0 && (
          <div className="flex max-w-820 flex-wrap gap-12">
            {links.map((link, index) => (
              <Button
                key={`${app.app}-${link.store}`}
                size="m"
                variant={index === 0 ? 'primary' : 'secondary'}
                url={link.url}
              >
                {link.title}
              </Button>
            ))}
          </div>
        )}

        <ol key={app.app} className="flex max-w-820 flex-col">
          {app.steps.map((step, index) => (
            <Step key={step.title} index={index} {...step} />
          ))}
        </ol>
      </div>
    </section>
  );
};

const Step: FCClass<TAppStepsStep & { index: number }> = ({
  index, title, text, note, image,
}) => (
  <Reveal type="fadeUp" tag="li" className="rule grid gap-16 py-32 t:grid-cols-[5rem_1fr]">
    <span className="font-display text-[2.5rem] leading-none font-extrabold text-accent">{index + 1}</span>
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
