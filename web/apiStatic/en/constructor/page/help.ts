import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Список инструкций (задача сайта 09). Ссылки на магазины — на страницах устройств, из config/site.yaml
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Setup guides — TS Proxy',
      description: 'How to connect TS Proxy on iPhone and iPad, Android, Windows and macOS: the Happ app, steps, and what to do if it does not work.',
    },
    content: [
      {
        type: 'pageHead',
        uuid: 'head',
        data: {
          label: 'Setup guides',
          title: 'Connect in a couple of minutes',
          text: 'The main app is Happ, the backup is v2RayTun. First get the free trial or pay for a plan in the [bot](bot), then choose your device. The bot is in Russian; button names are given with translations.',
        },
      },
      {
        type: 'devices',
        uuid: 'devices',
        data: {
          title: 'Choose your device',
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
            {
              title: 'macOS',
              text: 'Happ from the Mac App Store',
              url: '/help/macos/',
            },
          ],
        },
      },
      {
        type: 'textList',
        uuid: 'fallback',
        data: {
          id: 'fallback',
          label: 'Backup app',
          title: 'If Happ did not work',
          text: 'Install v2RayTun and follow the same steps:',
          items: [
            'on iPhone, iPad and Android — choose v2RayTun instead of Happ on the connection page;',
            'on Windows and macOS — copy the link in the bot («📋 Скопировать ссылку», Copy link) and add it to v2RayTun.',
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
    ],
  },
};

export default data;
