// Плашка о неполадках (задача карты 02, задача сайта 13). Бот отдаёт на /status
// `{"banner": "none" | "maintenance" | "proxy_issues"}`; не ответил — значит, он молчит.

export type TBanner = 'bot_down' | 'maintenance' | 'proxy_issues';

/** Что ответил бот: ok=false — не ответил, ответил не 200 или вернул не JSON */
export type TStatusResult = { ok: false } | { ok: true; body: unknown };

const MANUAL_BANNERS: readonly TBanner[] = ['maintenance', 'proxy_issues'];

/** Какую плашку показать по ответу бота; null — плашки нет */
export const bannerFor = (result: TStatusResult): TBanner | null => {
  if (!result.ok) {
    return 'bot_down';
  }
  const banner = (result.body as { banner?: unknown } | null)?.banner;
  return MANUAL_BANNERS.find(item => item === banner) ?? null;
};
