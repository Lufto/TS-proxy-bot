import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Setup guide: Windows. Apps and links — config/site.yaml → apps. Step screenshots — the image field of a step (src, width, height, alt)
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Windows — setup guide — TS Proxy',
      description: 'How to connect TS Proxy on Windows: Happ, v2RayTun, Hiddify and v2rayN — links to official sites and steps.',
    },
    content: [
      {
        type: 'appSteps',
        uuid: 'steps',
        data: {
          label: 'Setup guide',
          title: 'Windows',
          text: 'Pick an app — the steps below will change. The main one is Happ: Russian sites open directly in it, without the proxy. If it will not install or does not work, take the next one.',
          apps: [
            {
              app: 'happ',
              tag: 'Main',
              hint: 'Added with a button · Russia direct',
              downloads: [
                {
                  store: 'windows',
                  title: 'Download Happ for Windows',
                },
                {
                  store: 'site',
                  title: 'Happ website',
                },
              ],
              steps: [
                {
                  title: 'Install Happ',
                  text: 'Download the Happ installer — button above — and install the app.',
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
              app: 'v2raytun',
              tag: 'Backup',
              hint: 'Added with a button · Russia direct',
              downloads: [
                {
                  store: 'windows',
                  title: 'Download v2RayTun for Windows',
                },
              ],
              steps: [
                {
                  title: 'Install v2RayTun',
                  text: 'Download the v2RayTun installer from the official site — button above — and install the app.',
                },
                {
                  title: 'Add the subscription',
                  text: 'Go back to the bot, tap «Подключиться» (Connect), then «Открыть страницу подключения» (Open connection page). There, pick v2RayTun and tap «Добавить в v2RayTun» (Add to v2RayTun) — the app opens and adds the subscription itself.',
                  note: 'The button does not open the app? Tap «Скопировать ссылку» (Copy link) in the bot and paste it into v2RayTun with «+».',
                },
                {
                  title: 'Connect',
                  text: 'Tap the big connect button in v2RayTun and allow adding the configuration.',
                },
              ],
            },
            {
              app: 'hiddify',
              tag: 'Another option',
              hint: 'Added with a button',
              downloads: [
                {
                  store: 'windows',
                  title: 'Download Hiddify for Windows',
                },
              ],
              steps: [
                {
                  title: 'Install Hiddify',
                  text: 'Download the installer — button above — and install the app.',
                },
                {
                  title: 'Add the subscription',
                  text: 'Go back to the bot, tap «Подключиться» (Connect), then «Открыть страницу подключения» (Open connection page). There, pick Hiddify and tap «Добавить в Hiddify» (Add to Hiddify) — the app opens and adds the subscription itself.',
                },
                {
                  title: 'Connect',
                  text: 'Tap the big connect button in Hiddify and allow adding the configuration.',
                  note: 'In Hiddify all traffic goes through the proxy, Russian sites too. If a Russian site does not open, turn the proxy off for a while.',
                },
              ],
            },
            {
              app: 'v2rayn',
              tag: 'Another option',
              hint: 'Paste the link by hand',
              downloads: [
                {
                  store: 'windows',
                  title: 'Download v2rayN for Windows',
                },
              ],
              steps: [
                {
                  title: 'Install v2rayN',
                  text: 'Click the button above — a .zip archive downloads. Unpack it into any folder and run v2rayN.exe.',
                },
                {
                  title: 'Add the subscription',
                  text: 'In the bot, tap «Скопировать ссылку» (Copy link) in the «Подключиться» (Connect) section. In v2rayN, open Subscription group → Subscription group settings → Add, paste the link into the URL field, save and click Update subscription.',
                },
                {
                  title: 'Connect',
                  text: 'Tap the big connect button in v2rayN and allow adding the configuration.',
                  note: 'In v2rayN all traffic goes through the proxy, Russian sites too. If a Russian site does not open, turn the proxy off for a while.',
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
              title: 'iPhone and iPad',
              text: 'Happ or OneXray',
              url: '/help/ios/',
            },
            {
              title: 'Android',
              text: 'Happ, v2RayTun and 2 more',
              url: '/help/android/',
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
