// Рисует картинку превью и значок для телефона из og.html (задача сайта 12).
// Нужен Playwright: в проекте его нет, берётся глобальный (npm i -g playwright)
import { execSync } from 'node:child_process';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.resolve(here, '..', '..', 'public', 'meta');
const { chromium } = createRequire(`${execSync('npm root -g').toString().trim()}/`)('playwright');

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1300, height: 900 } });
await page.goto(pathToFileURL(path.join(here, 'og.html')).href);
await page.evaluate(() => document.fonts.ready);
await page.locator('#og').screenshot({ path: path.join(out, 'og.png') });
await page.locator('#icon').screenshot({ path: path.join(out, 'apple-touch-icon.png') });
await browser.close();
console.log('public/meta/og.png, public/meta/apple-touch-icon.png');
