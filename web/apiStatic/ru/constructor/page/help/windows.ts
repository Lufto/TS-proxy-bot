import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Инструкция: Windows. Скриншоты шагов — поле image у шага (src, width, height, alt), когда владелец пришлёт
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Windows — Инструкция — TS Proxy',
      description: 'Как подключить TS Proxy на Windows через приложение Happ.',
    },
    content: [
      {
        type: 'appSteps',
        uuid: 'steps',
        data: {
          label: 'Инструкция',
          title: 'Windows',
          text: 'Три шага через приложение Happ для Windows. Если не получилось — есть [запасное приложение](/help/#fallback).',
          downloads: [
            {
              app: 'happ',
              store: 'windows',
              title: 'Скачать Happ для Windows',
            },
            {
              app: 'v2raytun',
              store: 'windows',
              title: 'v2RayTun — запасное',
            },
          ],
          steps: [
            {
              title: 'Установите Happ',
              text: 'Скачайте установщик Happ для Windows — кнопка выше — и установите приложение. Ссылка есть и на странице подключения в боте («🚀 Подключиться» → «Открыть страницу подключения»).',
            },
            {
              title: 'Добавьте подписку',
              text: 'В боте нажмите «📋 Скопировать ссылку» (раздел «🚀 Подключиться»), в Happ нажмите «+» и вставьте ссылку из буфера.',
            },
            {
              title: 'Подключитесь',
              text: 'Нажмите кнопку подключения в Happ.',
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
              title: 'Android',
              text: 'Happ из Google Play',
              url: '/help/android/',
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
