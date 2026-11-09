import * as v from 'valibot';

// Ссылка на приложение: какое приложение и какая платформа — адрес берётся из config/site.yaml
const downloadSchema = v.object({
  app: v.picklist(['happ', 'v2raytun']),
  store: v.picklist(['ios', 'android', 'android_apk', 'windows', 'macos']),
  title: v.string(),
});

// Картинка шага — когда владелец пришлёт скриншоты. Размеры обязательны: страница не прыгает
const imageSchema = v.object({
  src: v.string(),
  width: v.number(),
  height: v.number(),
  alt: v.string(),
});

export const appStepsSchema = v.object({
  label: v.optional(v.string()),
  title: v.string(),
  text: v.optional(v.string()),
  downloads: v.array(downloadSchema),
  steps: v.array(v.object({
    title: v.string(),
    text: v.string(),
    note: v.optional(v.string()),
    image: v.optional(imageSchema),
  })),
});

export type TAppSteps = v.InferOutput<typeof appStepsSchema>;
