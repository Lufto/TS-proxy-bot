import { discountPercent, formatRubles, tariffOffers } from '@/shared/lib/tariffs';

const TARIFFS = [
  { id: 'm1', title: '1 месяц', days: 30, price: 150 },
  { id: 'm3', title: '3 месяца', days: 90, price: 405 },
  { id: 'm6', title: '6 месяцев', days: 180, price: 765 },
  { id: 'm12', title: '12 месяцев', days: 365, price: 1440 },
];

describe('тарифы на сайте', () => {
  it('выгода и цена в месяц совпадают с ботом и старым сайтом', () => {
    const offers = tariffOffers(TARIFFS);

    expect(offers.map(offer => offer.discount)).toEqual([0, 10, 15, 20]);
    expect(offers.map(offer => offer.perMonth)).toEqual([150, 135, 127.5, 120]);
    expect(offers.map(offer => offer.months)).toEqual([1, 3, 6, 12]);
  });

  it('скидка округляется вниз и не бывает отрицательной', () => {
    expect(discountPercent(90, 406, 150)).toBe(9);
    expect(discountPercent(90, 500, 150)).toBe(0);
    expect(discountPercent(7, 50, 150)).toBe(0);
  });

  it('без тарифа на месяц скидки нет', () => {
    expect(tariffOffers([TARIFFS[1]])[0].discount).toBe(0);
  });

  it('рубли пишутся как в боте', () => {
    expect(formatRubles(150)).toBe('150');
    expect(formatRubles(1440)).toBe('1 440');
    expect(formatRubles(127.5)).toBe('127,5');
  });
});
