// Общий конфиг сервиса → данные сайта (задача сайта 04). Читает config/tariffs.yaml
// и config/site.yaml из корня репозитория и пишет src/generated-config.ts.
// Запускается перед сборкой и dev-сервером; руками сгенерированный файл не правят.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import yaml from 'js-yaml';

const webDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const configDir = process.env.CONFIG_DIR || path.resolve(webDir, '..', 'config');
const outFile = path.join(webDir, 'src', 'generated-config.ts');

const read = name => yaml.load(fs.readFileSync(path.join(configDir, name), 'utf8'));

const fail = (message) => {
  console.error(`config/: ${message}`);
  process.exit(1);
};

const tariffsFile = read('tariffs.yaml');
const site = read('site.yaml');

const tariffs = (tariffsFile?.tariffs ?? []).map((tariff) => {
  const { id, title, days, price } = tariff ?? {};
  if (typeof id !== 'string' || typeof title !== 'string' || !Number.isInteger(days) || !Number.isInteger(price)) {
    fail(`тариф ${JSON.stringify(tariff)}: нужны id, title, days и целая цена`);
  }
  return { id, title, days, price };
});
if (tariffs.length === 0) fail('в tariffs.yaml нет тарифов');

for (const key of ['site_url', 'bot_username', 'support_email', 'status_url']) {
  if (typeof site?.[key] !== 'string' || site[key] === '') fail(`в site.yaml нет ${key}`);
}

const config = {
  tariffs,
  siteUrl: site.site_url.replace(/\/$/, ''),
  botUsername: site.bot_username,
  supportEmail: site.support_email,
  statusUrl: site.status_url,
  apps: site.apps ?? {},
};

fs.writeFileSync(
  outFile,
  `// Сгенерировано scripts/generate-config.mjs из config/ — не править руками\n`
  + `export const SITE_CONFIG = ${JSON.stringify(config, null, 2)} as const;\n`,
);
console.log(`config/ → ${path.relative(webDir, outFile)}: тарифов ${tariffs.length}`);
