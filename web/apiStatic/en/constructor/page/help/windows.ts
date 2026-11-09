import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Инструкция: Windows. Скриншоты шагов — поле image у шага (src, width, height, alt), когда владелец пришлёт
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Windows — setup guide — TS Proxy',
      description: 'How to connect TS Proxy on Windows with the Happ app.',
    },
    content: [
      {
        type: 'appSteps',
        uuid: 'steps',
        data: {
          label: 'Setup guide',
          title: 'Windows',
          text: 'Three steps with Happ for Windows. If it did not work, there is a [backup app](/help/#fallback).',
          downloads: [
            {
              app: 'happ',
              store: 'windows',
              title: 'Download Happ for Windows',
            },
            {
              app: 'v2raytun',
              store: 'windows',
              title: 'v2RayTun — backup',
            },
          ],
          steps: [
            {
              title: 'Install Happ',
              text: 'Download the Happ installer for Windows — button above — and install the app. The link is also on the connection page in the bot («🚀 Подключиться» → «Открыть страницу подключения»).',
            },
            {
              title: 'Add the subscription',
              text: 'In the bot, tap «📋 Скопировать ссылку» (Copy link) in the «🚀 Подключиться» (Connect) section; in Happ, click «+» and paste the link from the clipboard.',
            },
            {
              title: 'Connect',
              text: 'Click the connect button in Happ.',
            },
          ],
        },
      },
      {
        type: 'textList',
        uuid: 'trouble',
        data: {
          id: 'trouble',
          label: 'Not working?',
          title: 'What you can do yourself',
          ordered: true,
          items: [
            'Check in the bot («👤 Мой профиль», My profile) that your subscription is active.',
            'In the app, refresh the subscription (the refresh button next to it) and connect again.',
            'Switch from Wi-Fi to mobile data or the other way round.',
            'Restart the app.',
            'A new device will not connect? Every plan includes up to 3 devices. In the bot, open «👤 Мой профиль» → «📱 Мои устройства» (My devices) and remove one you no longer use.',
          ],
          note: 'The subscription link is your personal access. Do not share it with others.',
        },
      },
      {
        type: 'cta',
        uuid: 'cta',
        data: {
          title: 'Still not working?',
          text: 'Contact support: tell us your device and app and what exactly does not work. A screenshot of the error helps a lot.',
          buttons: [
            {
              title: 'Contact support',
              url: 'support',
            },
            {
              title: 'Open the bot',
              url: 'bot',
            },
          ],
        },
      },
      {
        type: 'devices',
        uuid: 'other',
        data: {
          title: 'All devices',
          items: [
            {
              title: 'iPhone and iPad',
              text: 'Happ from the App Store',
              url: '/help/ios/',
            },
            {
              title: 'Android',
              text: 'Happ from Google Play',
              url: '/help/android/',
            },
            {
              title: 'macOS',
              text: 'Happ from the Mac App Store',
              url: '/help/macos/',
            },
          ],
        },
      },
    ],
  },
};

export default data;
