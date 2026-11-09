// Тарифы на сайте (задача сайта 04): те же числа и та же скидка, что в боте
// (`bot/src/tsproxy/domain/money.py`), чтобы цены на сайте и в боте не расходились.

export const MONTH_DAYS = 30;

export type TTariff = {
  id: string;
  title: string;
  days: number;
  price: number;
};

export type TTariffOffer = TTariff & {
  /** Целых месяцев в тарифе: 365 дней — 12 */
  months: number;
  /** Цена за месяц; у тарифа короче месяца — null */
  perMonth: number | null;
  /** Выгода в процентах от цены месяца, округлена вниз */
  discount: number;
};

/** Скидка от цены месяца по целым месяцам, вниз до целого процента — как в боте */
export const discountPercent = (days: number, price: number, monthlyPrice: number): number => {
  const months = Math.floor(days / MONTH_DAYS);
  if (months === 0) {
    return 0;
  }
  const fullPrice = monthlyPrice * months;
  // Цены целые, считаем в целых числах: без ошибок округления дробей
  return Math.max(0, Math.floor(((fullPrice - price) * 100) / fullPrice));
};

export const tariffOffers = (tariffs: readonly TTariff[]): TTariffOffer[] => {
  const monthly = tariffs.find(tariff => tariff.days === MONTH_DAYS)?.price;

  return tariffs.map((tariff) => {
    const months = Math.floor(tariff.days / MONTH_DAYS);
    return {
      ...tariff,
      months,
      perMonth: months > 0 ? Math.round((tariff.price / months) * 100) / 100 : null,
      discount: monthly === undefined ? 0 : discountPercent(tariff.days, tariff.price, monthly),
    };
  });
};

const NBSP = ' ';

/** «150», «1 440», «127,5» — пробел в тысячах неразрывный, как в боте */
export const formatRubles = (amount: number): string => {
  const [whole, fraction] = amount.toFixed(2).replace(/0+$/, '').replace(/\.$/, '').split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
  return fraction ? `${grouped},${fraction}` : grouped;
};
