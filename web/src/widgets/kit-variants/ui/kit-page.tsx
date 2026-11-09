import cx from 'classix';
import type { ReactNode } from 'react';
import './kit.css';
import { GOOGLE_FONTS_HREF } from '../config/fonts';
import { MANIFEST_BUTTONS, MANIFEST_SECONDARY_BUTTONS, type TButtonOption } from '../model/buttons';
import { MANIFEST_LOGOS, markSvg, type TWordmarkStyle } from '../model/logos';
import type { TManifestChoice } from '../model/page';
import { KIT_VARIANT_IDS, KIT_VARIANTS, type TKitVariant, type TKitVariantId } from '../model/variants';

type TKitPageProps = {
  variantId: TKitVariantId;
  choice: TManifestChoice;
};

// Адрес кита с примеркой «Манифеста»
const manifestHref = ({ logoId, buttonId, secondaryId }: TManifestChoice) =>
  `?v=c&logo=${logoId}&btn=${buttonId}&btn2=${secondaryId}`;

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
type TMarkProps = { variant: TKitVariant; logoId: string; size: number; inverted?: boolean };

const Mark: FCClass<TMarkProps> = ({
  variant, logoId, size, inverted, className,
}) => (
  <span
    className={cx('inline-block shrink-0 [&>svg]:size-full', className)}
    style={{ width: size, height: size }}
    aria-hidden
    dangerouslySetInnerHTML={{ __html: markSvg(variant, logoId, inverted) }}
  />
);

// Надпись «Манифеста» в духе выбранного знака
const MANIFEST_WORDMARKS: Record<TWordmarkStyle, ReactNode> = {
  slash: (
    <>
      TS
      <span className="kit-accent">/</span>
      PROXY
    </>
  ),
  plain: 'TS PROXY',
  plate: (
    <>
      TS
      {' '}
      <span className="kit-wordmark-plate">PROXY</span>
    </>
  ),
  brackets: (
    <>
      <span className="kit-accent">[</span>
      TS
      <span className="kit-accent">]</span>
      {' '}
      PROXY
    </>
  ),
  arrow: (
    <>
      TS
      <svg className="kit-wordmark-arrow" viewBox="0 0 10 12" aria-hidden>
        <polygon points="0,0 4,0 10,6 4,12 0,12 6,6" fill="currentColor" />
      </svg>
      PROXY
    </>
  ),
};

const Wordmark: FCClass<Omit<TMarkProps, 'inverted'>> = ({ variant, logoId, size }) => {
  const style = MANIFEST_LOGOS.find(option => option.id === logoId)?.wordmark ?? 'slash';
  const text = variant.id === 'c' ? MANIFEST_WORDMARKS[style] : 'TS Proxy';

  return (
    <span className="inline-flex items-center" style={{ gap: size * 0.4 }}>
      <Mark variant={variant} logoId={logoId} size={size} />
      <span
        className="kit-h3 whitespace-nowrap"
        style={{ fontSize: size * 0.72, lineHeight: 1, textTransform: 'none' }}
      >
        {text}
      </span>
    </span>
  );
};

const Arrow = () => (
  <span className="kit-btn-arrow" aria-hidden>
    <span>→</span>
  </span>
);

type TButtonProps = { size: 'l' | 'm'; label: string; anim?: string; disabled?: boolean };

// Надпись дублируется в data-label для анимации «Бегущая строка»
const ButtonLabel = ({ label }: { label: string }) => (
  <span className="kit-btn-label" data-label={label}>
    <span>{label}</span>
  </span>
);

// Главная кнопка; анимация (`anim`) есть только у «Манифеста»
const PrimaryButton = ({
  size, label, anim, disabled,
}: TButtonProps) => (
  <button
    type="button"
    className={cx(`kit-btn kit-btn-${size} kit-btn-primary`, anim && `kit-btn-anim-${anim}`)}
    disabled={disabled}
  >
    <ButtonLabel label={label} />
    {!disabled && <Arrow />}
  </button>
);

const SecondaryButton = ({
  size, label, anim, disabled,
}: TButtonProps) => (
  <button
    type="button"
    className={cx(`kit-btn kit-btn-${size} kit-btn-secondary`, anim && `kit-btn2-anim-${anim}`)}
    disabled={disabled}
  >
    <ButtonLabel label={label} />
  </button>
);

type TLogoProps = { variant: TKitVariant; logoId: string };

const LogoSection = ({ variant, logoId }: TLogoProps) => (
  <div className="grid gap-16 t:grid-cols-2">
    <div className="kit-card flex min-h-200 min-w-0 items-center justify-center p-16 t:p-32">
      <span className="t:hidden"><Wordmark variant={variant} logoId={logoId} size={30} /></span>
      <span className="hidden t:inline-flex"><Wordmark variant={variant} logoId={logoId} size={44} /></span>
    </div>
    <div
      className="flex min-h-200 items-center justify-center p-32"
      style={{ background: 'var(--kit-accent)', borderRadius: 'var(--kit-radius)' }}
    >
      <Mark variant={variant} logoId={logoId} size={96} inverted />
    </div>
    <div className="kit-card flex flex-wrap items-end gap-24 p-24 t:col-span-2">
      {[64, 32, 16].map(size => (
        <span key={size} className="flex flex-col items-center gap-8">
          <Mark variant={variant} logoId={logoId} size={size} />
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

// Варианты знака «Манифеста»: «Примерить» ставит знак в шапку, в блок 01 и во вкладку
const LogoOptionsSection = ({ variant, choice }: { variant: TKitVariant; choice: TManifestChoice }) => (
  <div className="grid gap-16 t:grid-cols-2">
    {MANIFEST_LOGOS.map(option => (
      <article
        key={option.id}
        className="kit-card kit-logo-option flex min-w-0 flex-col gap-20 p-20 t:p-24"
        aria-current={option.id === choice.logoId ? 'true' : undefined}
      >
        <div className="flex items-center justify-between gap-12">
          <span className="kit-label">
            {option.id.padStart(2, '0')}
            {' · '}
            {option.name}
          </span>
          {option.id === choice.logoId && <span className="kit-label kit-accent">Примерен</span>}
        </div>
        <div className="flex min-h-72 items-center">
          <Wordmark variant={variant} logoId={option.id} size={30} />
        </div>
        <div className="flex items-end gap-16">
          <span
            className="inline-flex p-10"
            style={{ background: 'var(--kit-accent)' }}
          >
            <Mark variant={variant} logoId={option.id} size={44} inverted />
          </span>
          <Mark variant={variant} logoId={option.id} size={64} />
          <Mark variant={variant} logoId={option.id} size={32} />
          <Mark variant={variant} logoId={option.id} size={16} />
          <span className="kit-favicon-light">
            <Mark variant={variant} logoId={option.id} size={16} />
          </span>
        </div>
        <p className="kit-caption">{option.idea}</p>
        <a className="kit-link-arrow kit-body mt-auto" href={manifestHref({ ...choice, logoId: option.id })}>
          Примерить
          <span aria-hidden>→</span>
        </a>
      </article>
    ))}
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

type TAnimProps = { anim?: string; anim2?: string };

const ButtonsSection = ({ anim, anim2 }: TAnimProps) => (
  <div className="flex flex-col gap-28">
    {(['l', 'm'] as const).map(size => (
      <div key={size} className="flex flex-col gap-12">
        <span className="kit-label">{size === 'l' ? 'Крупные' : 'Обычные'}</span>
        <div className="flex flex-wrap items-center gap-12">
          <PrimaryButton size={size} label="Открыть бота" anim={anim} />
          <SecondaryButton size={size} label="Как подключиться" anim={anim2} />
          <button type="button" className={`kit-btn kit-btn-${size} kit-btn-ghost`}>
            Написать в поддержку
          </button>
        </div>
      </div>
    ))}
    <div className="flex flex-col gap-12">
      <span className="kit-label">Недоступна</span>
      <div className="flex flex-wrap items-center gap-12">
        <PrimaryButton size="m" label="Открыть бота" disabled />
        <SecondaryButton size="m" label="Как подключиться" anim={anim2} disabled />
      </div>
    </div>
    <p className="kit-caption">Наведите курсор и пройдитесь клавишей Tab — у кнопок есть состояния наведения и фокуса.</p>
  </div>
);

// Варианты кнопки «Манифеста»: у каждой карточки своя живая кнопка
const ButtonOptionsSection = ({
  options, currentId, renderButton, href,
}: {
  options: TButtonOption[];
  currentId: string;
  renderButton: (id: string) => ReactNode;
  href: (id: string) => string;
}) => (
  <div className="grid gap-16 t:grid-cols-2">
    {options.map(option => (
      <article
        key={option.id}
        className="kit-card kit-logo-option flex min-w-0 flex-col gap-20 p-20 t:p-24"
        aria-current={option.id === currentId ? 'true' : undefined}
      >
        <div className="flex items-center justify-between gap-12">
          <span className="kit-label">
            {option.id.padStart(2, '0')}
            {' · '}
            {option.name}
          </span>
          {option.id === currentId && <span className="kit-label kit-accent">Примерена</span>}
        </div>
        <div className="flex min-h-88 items-center">{renderButton(option.id)}</div>
        <p className="kit-caption">{option.idea}</p>
        <a className="kit-link-arrow kit-body mt-auto" href={href(option.id)}>
          Примерить
          <span aria-hidden>→</span>
        </a>
      </article>
    ))}
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

const HeroBlock = ({ anim, anim2 }: TAnimProps) => (
  <div className="kit-card relative overflow-hidden px-20 py-40 t:px-48 t:py-64">
    <div className="flex max-w-820 flex-col gap-24">
      <span className="kit-label kit-accent">Прокси с шифрованием трафика</span>
      <h3 className="kit-display">Ваш трафик&nbsp;— только ваш</h3>
      <p className="kit-body-l kit-muted max-w-560">
        Шифруем соединение и скрываем IP-адрес — в кафе, аэропорту и дома. Подключение
        за пару минут через Telegram-бота.
      </p>
      <div className="flex flex-wrap gap-12 pt-8">
        <PrimaryButton size="l" label="Открыть бота" anim={anim} />
        <SecondaryButton size="l" label="Как подключиться" anim={anim2} />
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

export const KitPage = ({ variantId, choice }: TKitPageProps) => {
  const variant = KIT_VARIANTS[variantId];
  const { logoId } = choice;
  const isManifest = variant.id === 'c';
  const anims = isManifest ? { anim: choice.buttonId, anim2: choice.secondaryId } : {};

  return (
    <main className="kit" data-kit={variant.id} style={variant.tokens}>
      <link rel="stylesheet" href={GOOGLE_FONTS_HREF} precedence="default" />
      <div className="mx-auto w-full max-w-1200 px-16 t:px-32">
        <nav className="flex flex-wrap items-center justify-between gap-16 py-24" aria-label="Варианты кита">
          <Wordmark variant={variant} logoId={logoId} size={28} />
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
          <LogoSection variant={variant} logoId={logoId} />
        </Section>
        {isManifest && (
          <Section
            number="01б"
            title="Варианты знака"
            note="Шесть знаков под «Манифест». «Примерить» ставит знак в шапку, в блок 01 и во вкладку браузера. Последний маленький — фавикон на светлой вкладке."
          >
            <LogoOptionsSection variant={variant} choice={choice} />
          </Section>
        )}
        <Section number="02" title="Палитра">
          <PaletteSection variant={variant} />
        </Section>
        <Section number="03" title="Шрифты и заголовки">
          <TypeSection variant={variant} />
        </Section>
        <Section number="04" title="Кнопки">
          <ButtonsSection {...anims} />
        </Section>
        {isManifest && (
          <>
            <Section
              number="04б"
              title="Анимация кнопки"
              note="Пять вариантов вместо «тени-сдвига». Наведите курсор на кнопку. «Примерить» ставит анимацию на все главные кнопки страницы."
            >
              <ButtonOptionsSection
                options={MANIFEST_BUTTONS}
                currentId={choice.buttonId}
                renderButton={id => <PrimaryButton size="l" label="Открыть бота" anim={id} />}
                href={id => manifestHref({ ...choice, buttonId: id })}
              />
            </Section>
            <Section
              number="04в"
              title="Вторая кнопка"
              note="Варианты вместо оранжевой рамки; шестой — выбор владельца. Наведите курсор на кнопку. «Примерить» ставит вариант на все вторые кнопки страницы."
            >
              <ButtonOptionsSection
                options={MANIFEST_SECONDARY_BUTTONS}
                currentId={choice.secondaryId}
                renderButton={id => <SecondaryButton size="l" label="Как подключиться" anim={id} />}
                href={id => manifestHref({ ...choice, secondaryId: id })}
              />
            </Section>
          </>
        )}
        <Section number="05" title="Ссылки">
          <div id="links">
            <LinksSection />
          </div>
        </Section>
        <Section number="06" title="Пример блока" note="Первый экран главной в этом стиле">
          <HeroBlock {...anims} />
        </Section>

        <footer className="kit-rule flex flex-wrap justify-between gap-12 py-32">
          <span className="kit-caption">Черновик для выбора стиля. Тексты — примерные.</span>
          <span className="kit-caption">© TS Proxy</span>
        </footer>
      </div>
    </main>
  );
};
