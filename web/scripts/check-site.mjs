// Проверка собранного сайта по HTTP (задачи сайта 03, 04, 07, 12): адреса отвечают на обоих языках,
// неизвестный адрес — 404, ни на одной странице нет слова «VPN», цены на главной — из config/tariffs.yaml,
// ссылки в бота и в поддержку — на бота из config/site.yaml, есть карта сайта и robots.
// Запуск: npm run build && npm start, затем в другом окне npm run check
// (другой адрес — CHECK_URL=https://ts-proxy.com npm run check).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import yaml from 'js-yaml';

const baseUrl = (process.env.CHECK_URL || 'http://localhost:3000').replace(/\/$/, '');
const configDir = process.env.CONFIG_DIR
  || path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'config');
const readConfig = name => yaml.load(fs.readFileSync(path.join(configDir, name), 'utf8'));
const { tariffs } = readConfig('tariffs.yaml');
const site = readConfig('site.yaml');

// Адреса без языка — русские; английские — те же с /en
const PAGES = ['/', '/help/', '/help/ios/', '/help/android/', '/help/windows/', '/help/macos/', '/terms/', '/privacy/'];
// На эти адреса ссылается бот: должны открываться и без слэша на конце
const BOT_LINKS = ['/terms', '/privacy'];
const NOT_FOUND = ['/net-takoy-stranicy/', '/en/no-such-page/'];
const FORBIDDEN_WORD = /\bvpn\b/i;
const BOT_URL = `https://t.me/${site.bot_username}`;
const SUPPORT_URL = `${BOT_URL}?start=support`;

const failures = [];

const fetchPage = async (path, redirect = 'follow') => {
  const res = await fetch(`${baseUrl}${path}`, { redirect });
  return { status: res.status, body: await res.text(), location: res.headers.get('location') };
};

// Куда в итоге ведёт адрес: идём по переадресациям сами, внешние (t.me) не открываем
const finalLocation = async (path) => {
  let location = path;
  for (let hop = 0; hop < 5 && location.startsWith('/'); hop += 1) {
    const res = await fetchPage(location, 'manual');
    if (!res.location) return location;
    location = res.location.replace(baseUrl, '');
  }
  return location;
};

const checkPage = async (path) => {
  const { status, body } = await fetchPage(path);
  if (status !== 200) {
    failures.push(`${path}: ответ ${status}, ждали 200`);
    return '';
  }
  if (FORBIDDEN_WORD.test(body)) {
    failures.push(`${path}: на странице есть слово «VPN»`);
  }
  // Все ссылки в Telegram ведут на нашего бота: просто так или в поддержку
  // В данных для React внутри страницы ссылки экранированы — обратный слэш в ссылку не входит
  for (const [link] of body.matchAll(/https:\/\/t\.me\/[^"'\s<>\\]*/g)) {
    const url = link.replaceAll('&amp;', '&');
    if (url !== BOT_URL && url !== SUPPORT_URL) {
      failures.push(`${path}: ссылка ${url} — не на бота ${site.bot_username}`);
    }
  }
  if (!body.includes(`href="${BOT_URL}"`)) {
    failures.push(`${path}: нет кнопки «Открыть бота» (${BOT_URL})`);
  }
  return body;
};

const paths = [...PAGES, ...PAGES.map(page => `/en${page}`), ...BOT_LINKS];
const bodies = {};
for (const path of paths) {
  bodies[path] = await checkPage(path);
}

// Цены на главной — те же, что в общем конфиге, в том же порядке
const expectedPrices = tariffs.map(tariff => String(tariff.price));
for (const path of ['/', '/en/']) {
  const prices = [...bodies[path].matchAll(/data-price="(\d+)"/g)].map(match => match[1]);
  if (prices.join(',') !== expectedPrices.join(',')) {
    failures.push(`${path}: цены ${prices.join(', ') || 'не найдены'}, в config/tariffs.yaml — ${expectedPrices.join(', ')}`);
  }
}

// Кнопка «Написать в поддержку» открывает поддержку в боте
for (const path of ['/', '/help/']) {
  if (!bodies[path].includes(`href="${SUPPORT_URL.replace('&', '&amp;')}"`) && !bodies[path].includes(`href="${SUPPORT_URL}"`)) {
    failures.push(`${path}: нет ссылки в поддержку ${SUPPORT_URL}`);
  }
}

for (const path of NOT_FOUND) {
  const { status } = await fetchPage(path);
  if (status !== 404) {
    failures.push(`${path}: ответ ${status}, ждали 404`);
  }
}

const sitemap = await fetchPage('/sitemap.xml');
for (const page of PAGES) {
  for (const prefix of ['', '/en']) {
    if (!sitemap.body.includes(`<loc>${site.site_url}${prefix}${page}</loc>`)) {
      failures.push(`/sitemap.xml: нет ${prefix}${page}`);
    }
  }
}
const robots = await fetchPage('/robots.txt');
if (robots.status !== 200 || !robots.body.includes('Disallow: /kit/')) {
  failures.push('/robots.txt: ждали ответ 200 и закрытый /kit/');
}

// Старые адреса сайта на чистом HTML
const oldTariffs = await finalLocation('/tariffs');
if (oldTariffs !== '/#tariffs') {
  failures.push(`/tariffs: ведёт на ${oldTariffs}, ждали /#tariffs`);
}
const oldSupport = await finalLocation('/support');
if (oldSupport !== SUPPORT_URL) {
  failures.push(`/support: ведёт на ${oldSupport}, ждали ${SUPPORT_URL}`);
}

if (failures.length > 0) {
  console.error(`Сайт ${baseUrl}: ошибок ${failures.length}`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}

console.log(`Сайт ${baseUrl}: проверено адресов ${paths.length + NOT_FOUND.length + 4}, всё в порядке`);
