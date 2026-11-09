import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Инструкция: macOS. Скриншоты шагов — поле image у шага (src, width, height, alt), когда владелец пришлёт
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'macOS — setup guide — TS Proxy',
      description: 'How to connect TS Proxy on a Mac with the Happ app.',
    },
    content: [
      {
        type: 'appSteps',
        uuid: 'steps',
        data: {
          label: 'Setup guide',
          title: 'macOS',
          text: 'Three steps with the Happ app. If it will not install, there is a [backup app](/help/#fallback).',
          downloads: [
            {
              app: 'happ',
              store: 'macos',
              title: 'Happ on the Mac App Store',
            },
            {
              app: 'v2raytun',
              store: 'macos',
              title: 'v2RayTun — backup',
            },
          ],
          steps: [
            {
              title: 'Install Happ',
              text: 'Install the Happ app from the Mac App Store — button above.',
              note: 'Cannot find it in the App Store? Change your Apple ID country: Mac App Store → your name at the bottom left → Account Settings → Change Country or Region (payment method — None). Then find Happ and install it.',
            },
            {
              title: 'Add the subscription',
              text: 'In the bot, tap «📋 Скопировать ссылку» (Copy link) in the «🚀 Подключиться» (Connect) section; in Happ, click «+» and paste the link from the clipboard.',
            },
            {
              title: 'Connect',
              text: 'Click the connect button and allow adding the configuration.',
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
              title: 'Windows',
              text: 'Happ for Windows',
              url: '/help/windows/',
            },
          ],
        },
      },
    ],
  },
};

export default data;
