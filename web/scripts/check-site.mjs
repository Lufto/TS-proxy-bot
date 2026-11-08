// Проверка собранного сайта по HTTP (задача сайта 03): адреса отвечают на обоих языках,
// неизвестный адрес — 404, ни на одной странице нет слова «VPN».
// Запуск: npm run build && npm start, затем в другом окне npm run check
// (другой адрес — CHECK_URL=https://ts-proxy.com npm run check).

const baseUrl = (process.env.CHECK_URL || 'http://localhost:3000').replace(/\/$/, '');

// Адреса без языка — русские; английские — те же с /en
const PAGES = ['/', '/help/', '/terms/', '/privacy/'];
// На эти адреса ссылается бот: должны открываться и без слэша на конце
const BOT_LINKS = ['/terms', '/privacy'];
const NOT_FOUND = ['/net-takoy-stranicy/', '/en/no-such-page/'];
const FORBIDDEN_WORD = /\bvpn\b/i;

const failures = [];

const fetchPage = async (path) => {
  const res = await fetch(`${baseUrl}${path}`, { redirect: 'follow' });
  return { status: res.status, body: await res.text() };
};

const checkPage = async (path) => {
  const { status, body } = await fetchPage(path);
  if (status !== 200) {
    failures.push(`${path}: ответ ${status}, ждали 200`);
    return;
  }
  if (FORBIDDEN_WORD.test(body)) {
    failures.push(`${path}: на странице есть слово «VPN»`);
  }
};

const paths = [...PAGES, ...PAGES.map(page => `/en${page === '/' ? '/' : page}`), ...BOT_LINKS];
for (const path of paths) {
  await checkPage(path);
}

for (const path of NOT_FOUND) {
  const { status } = await fetchPage(path);
  if (status !== 404) {
    failures.push(`${path}: ответ ${status}, ждали 404`);
  }
}

if (failures.length > 0) {
  console.error(`Сайт ${baseUrl}: ошибок ${failures.length}`);
  for (const failure of failures) console.error(`  - ${failure}`);
  process.exit(1);
}

console.log(`Сайт ${baseUrl}: проверено адресов ${paths.length + NOT_FOUND.length}, всё в порядке`);
