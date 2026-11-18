import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Setup guide: iPhone and iPad. Apps and links — config/site.yaml → apps. Step screenshots — the image field of a step (src, width, height, alt)
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'iPhone and iPad — setup guide — TS Proxy',
      description: 'How to connect TS Proxy on iPhone and iPad: Happ and OneXray — links to official sites and steps.',
    },
    content: [
      {
        type: 'appSteps',
        uuid: 'steps',
        data: {
          label: 'Setup guide',
          title: 'iPhone and iPad',
          text: 'Pick an app — the steps below will change. The main one is Happ: Russian sites open directly in it, without the proxy. If it will not install or does not work, take the next one.',
          apps: [
            {
              app: 'happ',
              tag: 'Main',
              hint: 'Added with a button · Russia direct',
              downloads: [
                {
                  store: 'ios',
                  title: 'Happ on the App Store (US)',
                },
                {
                  store: 'site',
                  title: 'Happ website',
                },
              ],
              steps: [
                {
                  title: 'Install Happ',
                  text: 'Install Happ from the App Store — button above.',
                  note: 'Happ is not in the Russian App Store right now. If you installed it before, find it in the App Store: your name → Purchased. If not, create a separate Apple ID for another country (no card needed) and sign in with it in the App Store only: Settings → your name → Media & Purchases. Do not touch your main Apple ID and iCloud. Careful: the Russian App Store has fakes named «Happ» — the real one is made by Flyfrog LLC.',
                },
                {
                  title: 'Add the subscription',
                  text: 'Go back to the bot, tap «Подключиться» (Connect), then «Открыть страницу подключения» (Open connection page). There, pick Happ and tap «Добавить в Happ» (Add to Happ) — the app opens and adds the subscription itself.',
                  note: 'The button does not open the app? Tap «Скопировать ссылку» (Copy link) in the bot and paste it into Happ with «+».',
                },
                {
                  title: 'Connect',
                  text: 'Tap the big connect button in Happ and allow adding the configuration.',
                },
              ],
            },
            {
              app: 'onexray',
              tag: 'Backup',
              hint: 'Paste the link by hand',
              downloads: [
                {
                  store: 'ios',
                  title: 'OneXray on the App Store',
                },
              ],
              steps: [
                {
                  title: 'Install OneXray',
                  text: 'Install OneXray from the App Store — it is in the Russian store too.',
                },
                {
                  title: 'Add the subscription',
                  text: 'In the bot, tap «Скопировать ссылку» (Copy link) in the «Подключиться» (Connect) section. In OneXray, tap «+», choose adding a subscription by link (URL) and paste the link.',
                },
                {
                  title: 'Connect',
                  text: 'Tap the big connect button in OneXray and allow adding the configuration.',
                  note: 'In OneXray all traffic goes through the proxy, Russian sites too. If a Russian site does not open, turn the proxy off for a while.',
                },
              ],
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
            'Check in the bot («Мой профиль», My profile) that your subscription is active.',
            'In the app, refresh the subscription (the refresh button next to it) and connect again.',
            'Switch from Wi-Fi to mobile data or the other way round.',
            'Restart the app.',
            'A new device will not connect? Every plan includes up to 3 devices. In the bot, open «Мой профиль» → «Мои устройства» (My devices) and remove one you no longer use.',
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
              title: 'Android',
              text: 'Happ, v2RayTun and 2 more',
              url: '/help/android/',
            },
            {
              title: 'Windows',
              text: 'Happ, v2RayTun and 2 more',
              url: '/help/windows/',
            },
            {
              title: 'macOS',
              text: 'Happ, OneXray and 2 more',
              url: '/help/macos/',
            },
            {
              title: 'Linux',
              text: 'Happ, Hiddify and 2 more',
              url: '/help/linux/',
            },
          ],
        },
      },
    ],
  },
};

export default data;
