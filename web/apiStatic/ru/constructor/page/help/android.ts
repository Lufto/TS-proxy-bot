import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Инструкция: Android. Скриншоты шагов — поле image у шага (src, width, height, alt), когда владелец пришлёт
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Android — Инструкция — TS Proxy',
      description: 'Как подключить TS Proxy на Android через приложение Happ.',
    },
    content: [
      {
        type: 'appSteps',
        uuid: 'steps',
        data: {
          label: 'Инструкция',
          title: 'Android',
          text: 'Три шага через приложение Happ. Если оно не ставится — есть [запасное приложение](/help/#fallback).',
          downloads: [
            {
              app: 'happ',
              store: 'android',
              title: 'Happ в Google Play',
            },
            {
              app: 'happ',
              store: 'android_apk',
              title: 'Скачать Happ (APK)',
            },
            {
              app: 'v2raytun',
              store: 'android',
              title: 'v2RayTun — запасное',
            },
          ],
          steps: [
            {
              title: 'Установите Happ',
              text: 'Установите приложение Happ из Google Play — кнопка выше.',
              note: 'Нет в Google Play? Скачайте установочный файл Happ (APK) с сайта разработчика — вторая кнопка выше, или на [happ.su](https://happ.su).',
            },
            {
              title: 'Добавьте подписку',
              text: 'Вернитесь в бота, нажмите «🚀 Подключиться», затем «Открыть страницу подключения» и на ней — кнопку добавления в Happ.',
            },
            {
              title: 'Подключитесь',
              text: 'В Happ нажмите большую кнопку подключения и разрешите приложению работать.',
            },
          ],
        },
      },
      {
        type: 'textList',
        uuid: 'trouble',
        data: {
          id: 'trouble',
          label: 'Не работает?',
          title: 'Что сделать самому',
          ordered: true,
          items: [
            'Проверьте в боте («👤 Мой профиль»), что подписка активна.',
            'В приложении обновите подписку (кнопка обновления рядом с ней) и подключитесь заново.',
            'Переключите Wi-Fi на мобильный интернет или наоборот.',
            'Перезапустите приложение.',
            'Новое устройство не подключается? В любом тарифе — до 3 устройств. Откройте в боте «👤 Мой профиль» → «📱 Мои устройства» и удалите то, которым больше не пользуетесь.',
          ],
          note: 'Ссылка подписки — ваш личный доступ. Не пересылайте её другим.',
        },
      },
      {
        type: 'cta',
        uuid: 'cta',
        data: {
          title: 'Не помогло?',
          text: 'Напишите в поддержку: какое устройство и приложение, что именно не работает. Скриншот ошибки очень поможет.',
          buttons: [
            {
              title: 'Написать в поддержку',
              url: 'support',
            },
            {
              title: 'Открыть бота',
              url: 'bot',
            },
          ],
        },
      },
      {
        type: 'devices',
        uuid: 'other',
        data: {
          title: 'Все устройства',
          items: [
            {
              title: 'iPhone и iPad',
              text: 'Happ из App Store',
              url: '/help/ios/',
            },
            {
              title: 'Windows',
              text: 'Happ для Windows',
              url: '/help/windows/',
            },
            {
              title: 'macOS',
              text: 'Happ из Mac App Store',
              url: '/help/macos/',
            },
          ],
        },
      },
    ],
  },
};

export default data;
