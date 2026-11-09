import { BOT_SUPPORT_URL, BOT_URL } from '@/shared/config/bot';
import { Button } from '@/shared/ui/button';
import { Link, LinkColor, LinkUnderline } from '@/shared/ui/link';
import { Logo, LogoMark } from '@/shared/ui/logo';
import { Reveal, RevealItem } from '@/shared/ui/reveal';

// UI-кит «Манифест» из настоящих компонентов сайта (задача сайта 06).
// Закрыт от поисковиков; черновики вариантов — на /kit/variants

const PALETTE = [
  { token: 'bg', role: 'Фон', hex: '#0B0B0B' },
  { token: 'surface', role: 'Карточки', hex: '#161616' },
  { token: 'line', role: 'Линии', hex: '#2B2B2B' },
  { token: 'cream', role: 'Текст', hex: '#F3EFE6' },
  { token: 'muted', role: 'Вторичный текст', hex: '#9D978B' },
  { token: 'accent', role: 'Акцент', hex: '#FF5B22' },
  { token: 'on-accent', role: 'Текст на акценте', hex: '#0B0B0B' },
];

const TYPE_SCALE = [
  { className: 't-display', name: 't-display', sample: 'Ваш трафик — только ваш' },
  { className: 't-h1', name: 't-h1', sample: 'Подключение за две минуты' },
  { className: 't-h2', name: 't-h2', sample: 'Тарифы и цены' },
  { className: 't-h3', name: 't-h3', sample: 'Шифрование трафика' },
  {
    className: 't-body-l',
    name: 't-body-l',
    sample: 'Шифруем соединение и скрываем IP-адрес — в кафе, аэропорту и дома.',
  },
  {
    className: 't-body',
    name: 't-body',
    sample: 'Провайдер и владелец Wi-Fi видят только, что вы подключены к серверу, но не что вы передаёте.',
  },
  { className: 't-caption', name: 't-caption', sample: 'Цены в рублях, оплата через СБП или звёздами Telegram' },
  { className: 't-label', name: 't-label', sample: 'Скорость · до 1 Гбит/с' },
];

const STATS = [
  { value: 'до 1 Гбит/с', label: 'скорость' },
  { value: '3 устройства', label: 'в одной подписке' },
  { value: '7 дней', label: 'бесплатно на пробу' },
];

const Section: FCClass<{ number: string; title: string; note?: string }> = ({
  number, title, note, children,
}) => (
  <section className="rule grid gap-32 py-56 t:py-72 md:grid-cols-[240px_1fr] md:gap-48">
    <header className="flex flex-col gap-8">
      <span className="t-label">{number}</span>
      <h2 className="t-h3">{title}</h2>
      {note && <p className="t-caption max-w-260">{note}</p>}
    </header>
    <div className="min-w-0">{children}</div>
  </section>
);

const Card: FCClass = ({ className, children }) => (
  <div className={`border border-line bg-surface ${className ?? ''}`}>{children}</div>
);

export const KitPage = () => (
  <main className="container">
    <header className="flex flex-col gap-20 pt-56 pb-64 t:pt-80">
      <span className="t-label">UI-кит TS Proxy</span>
      <h1 className="t-h1">Манифест</h1>
      <p className="t-body-l max-w-720 text-muted">
        Компоненты и токены сайта. Шапка и подвал на этой странице — настоящие, как на всех страницах.
      </p>
    </header>

    <Section number="01" title="Логотип и фавикон" note="Знак «Сдвиг». Фавикон — тот же знак, он во вкладке браузера">
      <div className="grid gap-16 t:grid-cols-2">
        <Card className="flex min-h-200 items-center justify-center p-24">
          <span className="hidden t:block"><Logo size={44} /></span>
          <span className="t:hidden"><Logo size={30} /></span>
        </Card>
        <div className="flex min-h-200 items-center justify-center bg-accent p-32">
          <LogoMark size={96} />
        </div>
        <Card className="flex flex-wrap items-end gap-24 p-24 t:col-span-2">
          {[64, 32, 16].map(size => (
            <span key={size} className="flex flex-col items-center gap-8">
              <LogoMark size={size} />
              <span className="t-caption">
                {size}
                {' '}
                px
              </span>
            </span>
          ))}
          <p className="t-caption ml-auto max-w-280">
            Компоненты: Logo (знак с надписью) и LogoMark (только знак).
          </p>
        </Card>
      </div>
    </Section>

    <Section number="02" title="Палитра" note="Токены в theme.css: классы bg-accent, text-muted, border-line…">
      <div className="grid grid-cols-2 gap-12 t:grid-cols-4">
        {PALETTE.map(swatch => (
          <Card key={swatch.token} className="overflow-hidden">
            <div className="h-88" style={{ background: swatch.hex }} />
            <div className="flex flex-col gap-4 p-14">
              <span className="t-body font-semibold">{swatch.role}</span>
              <span className="t-label normal-case tracking-normal">
                {swatch.token}
                {' · '}
                {swatch.hex}
              </span>
            </div>
          </Card>
        ))}
      </div>
    </Section>

    <Section number="03" title="Шрифты и заголовки" note="Классы типографики в typography.css">
      <div className="flex flex-col gap-32">
        <div className="grid gap-12 t:grid-cols-3">
          {[
            ['Заголовки', 'Unbounded', 'font-display'],
            ['Текст', 'Golos Text', 'font-body'],
            ['Метки и цифры', 'JetBrains Mono', 'font-mono'],
          ].map(([role, font, token]) => (
            <Card key={role} className="flex flex-col gap-6 p-16">
              <span className="t-label">{role}</span>
              <span className="t-body font-semibold">{font}</span>
              <span className="t-caption">{token}</span>
            </Card>
          ))}
        </div>
        {TYPE_SCALE.map(item => (
          <div key={item.name} className="flex flex-col gap-8">
            <span className="t-label normal-case">{item.name}</span>
            <p className={item.className}>{item.sample}</p>
          </div>
        ))}
      </div>
    </Section>

    <Section number="04" title="Кнопки" note="Button: primary, secondary, ghost; размеры l и m. С url — ссылка">
      <div className="flex flex-col gap-28">
        {(['l', 'm'] as const).map(size => (
          <div key={size} className="flex flex-col gap-12">
            <span className="t-label">{size === 'l' ? 'Крупные · l' : 'Обычные · m'}</span>
            <div className="flex flex-wrap items-center gap-12">
              <Button size={size} url={BOT_URL}>Открыть бота</Button>
              <Button size={size} variant="secondary" url="/help/">Как подключиться</Button>
              <Button size={size} variant="ghost" url={BOT_SUPPORT_URL}>Написать в поддержку</Button>
            </div>
          </div>
        ))}
        <div className="flex flex-col gap-12">
          <span className="t-label">Недоступна · disabled</span>
          <div className="flex flex-wrap items-center gap-12">
            <Button size="m" disabled>Открыть бота</Button>
            <Button size="m" variant="secondary" disabled>Как подключиться</Button>
          </div>
        </div>
        <p className="t-caption">Наведите курсор и пройдитесь клавишей Tab — у кнопок есть состояния наведения и фокуса.</p>
      </div>
    </Section>

    <Section number="05" title="Ссылки" note="Link из хранилища компонентов: линия hover, solid, none; цвета cream, muted, accent">
      <div className="flex flex-col gap-24">
        <p className="t-body-l max-w-640">
          Подключение занимает пару минут: откройте
          {' '}
          <Link url="/help/" underline={LinkUnderline.solid}>инструкцию для своего устройства</Link>
          {' '}
          и следуйте шагам. Условия описаны в
          {' '}
          <Link url="/terms/" underline={LinkUnderline.solid}>пользовательском соглашении</Link>
          .
        </p>
        <Link url="/help/" color={LinkColor.accent} underline={LinkUnderline.none} arrow typography="t-body font-semibold">
          Все инструкции
        </Link>
        <div className="flex flex-wrap gap-24">
          <Link url="/terms/" typography="t-body">Ссылка в меню</Link>
          {['Соглашение', 'Политика конфиденциальности', 'Поддержка'].map(label => (
            <Link key={label} url="/privacy/" color={LinkColor.muted} typography="t-caption">{label}</Link>
          ))}
        </div>
      </div>
    </Section>

    <Section number="06" title="Появление" note="Reveal из хранилища компонентов: блок проявляется, когда доходит до экрана">
      <Reveal type="fadeUp" tag="ul" staggerChildren={0.12} className="grid gap-12 t:grid-cols-3">
        {['fade', 'fadeUp', 'scale'].map(type => (
          <RevealItem key={type} tag="li" className="border border-line bg-surface p-24">
            <span className="t-label">Reveal</span>
            <p className="t-h3 mt-8">{type}</p>
          </RevealItem>
        ))}
      </Reveal>
    </Section>

    <Section number="07" title="Пример блока" note="Первый экран главной в этом стиле">
      <Card className="relative overflow-hidden px-20 py-40 t:px-48 t:py-64">
        <Reveal type="fadeUp" className="flex max-w-820 flex-col gap-24">
          <span className="t-label text-accent">Прокси с шифрованием трафика</span>
          <h3 className="t-display">Ваш трафик&nbsp;— только ваш</h3>
          <p className="t-body-l max-w-560 text-muted">
            Шифруем соединение и скрываем IP-адрес — в кафе, аэропорту и дома. Подключение
            за пару минут через Telegram-бота.
          </p>
          <div className="flex flex-wrap gap-12 pt-8">
            <Button url={BOT_URL}>Открыть бота</Button>
            <Button variant="secondary" url="/help/">Как подключиться</Button>
          </div>
        </Reveal>
        <dl className="rule mt-48 grid gap-24 pt-32 t:grid-cols-3">
          {STATS.map(stat => (
            <div key={stat.label} className="flex flex-col gap-4">
              <dt className="t-h3">{stat.value}</dt>
              <dd className="t-caption">{stat.label}</dd>
            </div>
          ))}
        </dl>
      </Card>
    </Section>
  </main>
);
