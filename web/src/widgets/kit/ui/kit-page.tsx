import cx from 'classix';
import './kit.css';
import { markSvg } from '../model/logos';
import { KIT_VARIANT_IDS, KIT_VARIANTS, type TKitVariant, type TKitVariantId } from '../model/variants';

type TKitPageProps = {
  variantId: TKitVariantId;
};

const Section: FCClass<{ number: string; title: string; note?: string }> = ({
  number, title, note, children,
}) => (
  <section className="kit-rule grid gap-32 py-56 t:py-72 md:grid-cols-[240px_1fr] md:gap-48">
    <header className="flex flex-col gap-8">
      <span className="kit-label">{number}</span>
      <h2 className="kit-h3">{title}</h2>
      {note && <p className="kit-caption max-w-260">{note}</p>}
    </header>
    <div className="min-w-0">{children}</div>
  </section>
);

// Наш собственный статичный SVG из logos.ts, не данные извне
const Mark: FCClass<{ variant: TKitVariant; size: number; inverted?: boolean }> = ({
  variant, size, inverted, className,
}) => (
  <span
    className={cx('inline-block shrink-0 [&>svg]:size-full', className)}
    style={{ width: size, height: size }}
    aria-hidden
    dangerouslySetInnerHTML={{ __html: markSvg(variant, inverted) }}
  />
);

const Wordmark: FCClass<{ variant: TKitVariant; size: number }> = ({ variant, size }) => {
  const text = variant.id === 'c'
    ? (
        <>
          TS
          <span className="kit-accent">/</span>
          PROXY
        </>
      )
    : 'TS Proxy';

  return (
    <span className="inline-flex items-center" style={{ gap: size * 0.4 }}>
      <Mark variant={variant} size={size} />
      <span
        className="kit-h3"
        style={{ fontSize: size * 0.72, lineHeight: 1, textTransform: 'none' }}
      >
        {text}
      </span>
    </span>
  );
};

const Arrow = () => <span className="kit-btn-arrow" aria-hidden>→</span>;

const LogoSection = ({ variant }: { variant: TKitVariant }) => (
  <div className="grid gap-16 t:grid-cols-2">
    <div className="kit-card flex min-h-200 min-w-0 items-center justify-center p-16 t:p-32">
      <span className="t:hidden"><Wordmark variant={variant} size={34} /></span>
      <span className="hidden t:inline-flex"><Wordmark variant={variant} size={48} /></span>
    </div>
    <div
      className="flex min-h-200 items-center justify-center p-32"
      style={{ background: 'var(--kit-accent)', borderRadius: 'var(--kit-radius)' }}
    >
      <Mark variant={variant} size={96} inverted />
    </div>
    <div className="kit-card flex flex-wrap items-end gap-24 p-24 t:col-span-2">
      {[64, 32, 16].map(size => (
        <span key={size} className="flex flex-col items-center gap-8">
          <Mark variant={variant} size={size} />
          <span className="kit-caption">
            {size}
            {' '}
            px
          </span>
        </span>
      ))}
      <p className="kit-caption ml-auto max-w-280">
        Фавикон — тот же знак. Посмотрите на вкладку браузера: он уже стоит там.
      </p>
    </div>
  </div>
);

const PaletteSection = ({ variant }: { variant: TKitVariant }) => (
  <div className="grid grid-cols-2 gap-12 t:grid-cols-4">
    {variant.palette.map(swatch => (
      <div key={swatch.name} className="kit-card overflow-hidden">
        <div className="h-88" style={{ background: swatch.hex }} />
        <div className="flex flex-col gap-4 p-14">
          <span className="kit-body font-semibold">{swatch.role}</span>
          <span className="kit-label normal-case tracking-normal">{swatch.hex}</span>
        </div>
      </div>
    ))}
  </div>
);

const TYPE_SCALE = [
  { className: 'kit-display', name: 'Display', sample: 'Ваш трафик\u00A0— только ваш' },
  { className: 'kit-h1', name: 'Заголовок 1', sample: 'Подключение за две минуты' },
  { className: 'kit-h2', name: 'Заголовок 2', sample: 'Тарифы и цены' },
  { className: 'kit-h3', name: 'Заголовок 3', sample: 'Шифрование трафика' },
  {
    className: 'kit-body-l',
    name: 'Текст крупный',
    sample: 'Шифруем соединение и скрываем IP-адрес — в кафе, аэропорту и дома.',
  },
  {
    className: 'kit-body',
    name: 'Текст',
    sample: 'Провайдер и владелец Wi-Fi видят только, что вы подключены к серверу, но не что вы передаёте.',
  },
  { className: 'kit-caption', name: 'Подпись', sample: 'Цены в рублях, оплата через СБП или звёздами Telegram' },
  { className: 'kit-label', name: 'Метка', sample: 'Скорость · до 1 Гбит/с' },
];

const TypeSection = ({ variant }: { variant: TKitVariant }) => (
  <div className="flex flex-col gap-32">
    <div className="grid gap-12 t:grid-cols-3">
      {[
        ['Заголовки', variant.fonts.display],
        ['Текст', variant.fonts.body],
        ['Метки и цифры', variant.fonts.mono],
      ].map(([role, font]) => (
        <div key={role} className="kit-card flex flex-col gap-6 p-16">
          <span className="kit-label">{role}</span>
          <span className="kit-body font-semibold">{font}</span>
        </div>
      ))}
    </div>
    {TYPE_SCALE.map(item => (
      <div key={item.name} className="flex flex-col gap-8">
        <span className="kit-label">{item.name}</span>
        <p className={item.className}>{item.sample}</p>
      </div>
    ))}
  </div>
);

const ButtonsSection = () => (
  <div className="flex flex-col gap-28">
    {(['l', 'm'] as const).map(size => (
      <div key={size} className="flex flex-col gap-12">
        <span className="kit-label">{size === 'l' ? 'Крупные' : 'Обычные'}</span>
        <div className="flex flex-wrap items-center gap-12">
          <button type="button" className={`kit-btn kit-btn-${size} kit-btn-primary`}>
            Открыть бота
            <Arrow />
          </button>
          <button type="button" className={`kit-btn kit-btn-${size} kit-btn-secondary`}>
            Как подключиться
          </button>
          <button type="button" className={`kit-btn kit-btn-${size} kit-btn-ghost`}>
            Написать в поддержку
          </button>
        </div>
      </div>
    ))}
    <div className="flex flex-col gap-12">
      <span className="kit-label">Недоступна</span>
      <div className="flex flex-wrap items-center gap-12">
        <button type="button" className="kit-btn kit-btn-m kit-btn-primary" disabled>
          Открыть бота
        </button>
        <button type="button" className="kit-btn kit-btn-m kit-btn-secondary" disabled>
          Как подключиться
        </button>
      </div>
    </div>
    <p className="kit-caption">Наведите курсор и пройдитесь клавишей Tab — у кнопок есть состояния наведения и фокуса.</p>
  </div>
);

const LinksSection = () => (
  <div className="flex flex-col gap-24">
    <p className="kit-body-l max-w-640">
      Подключение занимает пару минут: откройте
      {' '}
      <a className="kit-link" href="#links">инструкцию для своего устройства</a>
      {' '}
      и следуйте шагам. Условия описаны в
      {' '}
      <a className="kit-link" href="#links">пользовательском соглашении</a>
      .
    </p>
    <a className="kit-link-arrow kit-body" href="#links">
      Все инструкции
      <span aria-hidden>→</span>
    </a>
    <div className="flex flex-wrap gap-20">
      {['Соглашение', 'Политика конфиденциальности', 'Поддержка'].map(label => (
        <a key={label} className="kit-link-muted kit-caption" href="#links">{label}</a>
      ))}
    </div>
  </div>
);

const STATS = [
  { value: 'до 1 Гбит/с', label: 'скорость' },
  { value: '3 устройства', label: 'в одной подписке' },
  { value: '7 дней', label: 'бесплатно на пробу' },
];

const HeroBlock = () => (
  <div className="kit-card relative overflow-hidden px-20 py-40 t:px-48 t:py-64">
    <div className="flex max-w-820 flex-col gap-24">
      <span className="kit-label kit-accent">Прокси с шифрованием трафика</span>
      <h3 className="kit-display">Ваш трафик&nbsp;— только ваш</h3>
      <p className="kit-body-l kit-muted max-w-560">
        Шифруем соединение и скрываем IP-адрес — в кафе, аэропорту и дома. Подключение
        за пару минут через Telegram-бота.
      </p>
      <div className="flex flex-wrap gap-12 pt-8">
        <button type="button" className="kit-btn kit-btn-l kit-btn-primary">
          Открыть бота
          <Arrow />
        </button>
        <button type="button" className="kit-btn kit-btn-l kit-btn-secondary">
          Как подключиться
        </button>
      </div>
    </div>
    <dl className="kit-rule mt-48 grid gap-24 pt-32 t:grid-cols-3">
      {STATS.map(stat => (
        <div key={stat.label} className="flex flex-col gap-4">
          <dt className="kit-h3">{stat.value}</dt>
          <dd className="kit-caption">{stat.label}</dd>
        </div>
      ))}
    </dl>
  </div>
);

export const KitPage = ({ variantId }: TKitPageProps) => {
  const variant = KIT_VARIANTS[variantId];

  return (
    <main className="kit" data-kit={variant.id} style={variant.tokens}>
      <div className="mx-auto w-full max-w-1200 px-16 t:px-32">
        <nav className="flex flex-wrap items-center justify-between gap-16 py-24" aria-label="Варианты кита">
          <Wordmark variant={variant} size={32} />
          <div className="flex flex-wrap gap-8">
            {KIT_VARIANT_IDS.map(id => (
              <a
                key={id}
                className="kit-tab"
                href={`?v=${id}`}
                aria-current={id === variant.id ? 'page' : undefined}
              >
                <span className="kit-label normal-case" style={{ color: 'inherit' }}>{id.toUpperCase()}</span>
                {KIT_VARIANTS[id].name}
              </a>
            ))}
          </div>
        </nav>

        <header className="flex flex-col gap-20 pt-40 pb-64 t:pt-72">
          <span className="kit-label">
            UI-кит TS Proxy · вариант
            {' '}
            {variant.id.toUpperCase()}
          </span>
          <h1 className="kit-h1">{variant.name}</h1>
          <p className="kit-body-l kit-muted max-w-720">{variant.idea}</p>
        </header>

        <Section number="01" title="Логотип и фавикон" note="Набросок: после выбора варианта дорабатываем">
          <LogoSection variant={variant} />
        </Section>
        <Section number="02" title="Палитра">
          <PaletteSection variant={variant} />
        </Section>
        <Section number="03" title="Шрифты и заголовки">
          <TypeSection variant={variant} />
        </Section>
        <Section number="04" title="Кнопки">
          <ButtonsSection />
        </Section>
        <Section number="05" title="Ссылки">
          <div id="links">
            <LinksSection />
          </div>
        </Section>
        <Section number="06" title="Пример блока" note="Первый экран главной в этом стиле">
          <HeroBlock />
        </Section>

        <footer className="kit-rule flex flex-wrap justify-between gap-12 py-32">
          <span className="kit-caption">Черновик для выбора стиля. Тексты — примерные.</span>
          <span className="kit-caption">© TS Proxy</span>
        </footer>
      </div>
    </main>
  );
};
