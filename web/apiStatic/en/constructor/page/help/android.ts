import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Инструкция: Android. Скриншоты шагов — поле image у шага (src, width, height, alt), когда владелец пришлёт
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Android — setup guide — TS Proxy',
      description: 'How to connect TS Proxy on Android with the Happ app.',
    },
    content: [
      {
        type: 'appSteps',
        uuid: 'steps',
        data: {
          label: 'Setup guide',
          title: 'Android',
          text: 'Three steps with the Happ app. If it will not install, there is a [backup app](/help/#fallback).',
          downloads: [
            {
              app: 'happ',
              store: 'android',
              title: 'Happ on Google Play',
            },
            {
              app: 'happ',
              store: 'android_apk',
              title: 'Download Happ (APK)',
            },
            {
              app: 'v2raytun',
              store: 'android',
              title: 'v2RayTun — backup',
            },
          ],
          steps: [
            {
              title: 'Install Happ',
              text: 'Install the Happ app from Google Play — button above.',
              note: 'Not on Google Play? Download the Happ installer (APK) from the developer — the second button above, or at [happ.su](https://happ.su).',
            },
            {
              title: 'Add the subscription',
              text: 'Go back to the bot, tap «🚀 Подключиться» (Connect), then «Открыть страницу подключения» (Open connection page), and on that page tap the button to add it to Happ.',
            },
            {
              title: 'Connect',
              text: 'In Happ, tap the big connect button and allow the app to run.',
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
              title: 'Windows',
              text: 'Happ for Windows',
              url: '/help/windows/',
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
