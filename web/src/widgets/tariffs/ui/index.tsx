import cx from 'classix';
import { getLocale } from 'next-intl/server';
import { SITE_CONFIG } from '@/generated-config';
import { resolveUrl } from '@/shared/lib/resolve-url';
import { formatRubles, tariffOffers, TTariffOffer } from '@/shared/lib/tariffs';
import { Button } from '@/shared/ui/button';
import { Reveal, RevealItem } from '@/shared/ui/reveal';
import { RichText } from '@/shared/ui/rich-text';
import { Section } from '@/shared/ui/section';
import { TTariffs } from '../model/schema';

// Название тарифа: на русском — из конфига, на английском — по числу месяцев
const tariffTitle = (offer: TTariffOffer, locale: string) => {
  if (locale === 'ru') return offer.title;
  if (offer.months === 0) return `${offer.days} days`;
  return `${offer.months} ${offer.months === 1 ? 'month' : 'months'}`;
};

// Тарифы из общего конфига: цена, цена в месяц и выгода считаются как в боте
export const Tariffs = async ({
  id, label, title, text, perMonth, discount, days, best, button, notes,
}: TTariffs) => {
  const locale = await getLocale();
  const offers = tariffOffers(SITE_CONFIG.tariffs);
  const bestDiscount = Math.max(...offers.map(offer => offer.discount));

  return (
    <Section id={id} label={label} title={title} text={text}>
      <Reveal type="fadeUp" tag="ul" staggerChildren={0.08} className="grid gap-12 t:grid-cols-2 md:grid-cols-4">
        {offers.map((offer) => {
          const isBest = offer.discount > 0 && offer.discount === bestDiscount;
          return (
            <RevealItem
              key={offer.id}
              tag="li"
              className={cx('relative flex flex-col gap-16 border bg-surface p-24', isBest ? 'border-accent' : 'border-line')}
            >
              <div className="flex items-center justify-between gap-8">
                <span className="t-label">{tariffTitle(offer, locale)}</span>
                {isBest && <span className="t-label bg-accent px-6 py-2 text-on-accent">{best}</span>}
              </div>
              <p className="t-h2 whitespace-nowrap" data-price={offer.price}>
                {formatRubles(offer.price)}
                {' '}
                ₽
              </p>
              <div className="mt-auto flex flex-col gap-4">
                <span className="t-caption">
                  {offer.days}
                  {' '}
                  {days}
                  {offer.perMonth !== null && offer.months > 1 && (
                    <>
                      {' · '}
                      {formatRubles(offer.perMonth)}
                      {' ₽ '}
                      {perMonth}
                    </>
                  )}
                </span>
                <span className={cx('t-label', offer.discount > 0 ? 'text-accent' : 'invisible')}>
                  {discount}
                  {' '}
                  {offer.discount}
                  %
                </span>
              </div>
            </RevealItem>
          );
        })}
      </Reveal>
      <div>
        <Button url={resolveUrl(button.url)}>{button.title}</Button>
      </div>
      {notes && notes.length > 0 && (
        <dl className="grid gap-x-48 gap-y-28 pt-16 md:grid-cols-2">
          {notes.map(note => (
            <div key={note.title} className="flex flex-col gap-8">
              <dt className="t-h3">{note.title}</dt>
              <dd className="t-body text-muted"><RichText text={note.text} /></dd>
            </div>
          ))}
        </dl>
      )}
    </Section>
  );
};
