import { bannerFor } from '@/shared/lib/status';

describe('плашка о неполадках', () => {
  it('бот не ответил — «бот не отвечает»', () => {
    expect(bannerFor({ ok: false })).toBe('bot_down');
  });

  it('бот ответил без плашки — плашки нет', () => {
    expect(bannerFor({ ok: true, body: { banner: 'none' } })).toBeNull();
  });

  it('плашки, поставленные администратором', () => {
    expect(bannerFor({ ok: true, body: { banner: 'maintenance' } })).toBe('maintenance');
    expect(bannerFor({ ok: true, body: { banner: 'proxy_issues' } })).toBe('proxy_issues');
  });

  it('непонятный ответ бота не пугает посетителя', () => {
    expect(bannerFor({ ok: true, body: { banner: 'что-то новое' } })).toBeNull();
    expect(bannerFor({ ok: true, body: null })).toBeNull();
    expect(bannerFor({ ok: true, body: 'ok' })).toBeNull();
  });
});
