import * as v from 'valibot';

// Ссылка на приложение: какое приложение и откуда ставить — адрес берётся из config/site.yaml
const downloadSchema = v.object({
  store: v.picklist(['ios', 'android', 'android_apk', 'windows', 'macos', 'macos_intel', 'linux', 'linux_deb', 'linux_rpm', 'github', 'site']),
  title: v.string(),
});

// Картинка шага — когда владелец пришлёт скриншоты. Размеры обязательны: страница не прыгает
const imageSchema = v.object({
  src: v.string(),
  width: v.number(),
  height: v.number(),
  alt: v.string(),
});

const stepSchema = v.object({
  title: v.string(),
  text: v.string(),
  note: v.optional(v.string()),
  image: v.optional(imageSchema),
});

// Приложение на странице устройства: метка роли («Главное», «Запасное»…), чем отличается, ссылки и шаги
const appSchema = v.object({
  app: v.picklist(['happ', 'v2raytun', 'onexray', 'hiddify', 'v2rayn', 'throne']),
  tag: v.string(),
  hint: v.optional(v.string()),
  downloads: v.array(downloadSchema),
  steps: v.array(stepSchema),
});

export const appStepsSchema = v.object({
  label: v.optional(v.string()),
  title: v.string(),
  text: v.optional(v.string()),
  apps: v.pipe(v.array(appSchema), v.minLength(1)),
});

export type TAppSteps = v.InferOutput<typeof appStepsSchema>;
export type TAppStepsStep = v.InferOutput<typeof stepSchema>;
