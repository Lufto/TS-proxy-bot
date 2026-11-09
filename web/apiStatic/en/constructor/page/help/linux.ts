import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Setup guide: Linux. Apps and links — config/site.yaml → apps. Step screenshots — the image field of a step (src, width, height, alt)
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Linux — setup guide — TS Proxy',
      description: 'How to connect TS Proxy on Linux: Happ, Hiddify, v2rayN and Throne — links to official sites and steps.',
    },
    content: [
      {
        type: 'appSteps',
        uuid: 'steps',
        data: {
          label: 'Setup guide',
          title: 'Linux',
          text: 'Pick an app — the steps below will change. The main one is Happ: Russian sites open directly in it, without the proxy. If it will not install or does not work, take the next one.',
          apps: [
            {
              app: 'happ',
              tag: 'Main',
              hint: 'Added with a button · Russia direct',
              downloads: [
                {
                  store: 'linux_deb',
                  title: 'For Ubuntu / Debian',
                },
                {
                  store: 'linux_rpm',
                  title: 'For Fedora',
                },
                {
                  store: 'site',
                  title: 'Happ website',
                },
              ],
              steps: [
                {
                  title: 'Install Happ',
                  text: 'Click the button for your system — the package downloads right away — and install it with a double click.',
                },
                {
                  title: 'Add the subscription',
                  text: 'Go back to the bot, tap «🚀 Подключиться» (Connect), then «Открыть страницу подключения» (Open connection page). There, pick Happ and tap «Добавить в Happ» (Add to Happ) — the app opens and adds the subscription itself.',
                  note: 'The button does not open the app? Tap «📋 Скопировать ссылку» (Copy link) in the bot and paste it into Happ with «+».',
                },
                {
                  title: 'Connect',
                  text: 'Tap the big connect button in Happ and allow adding the configuration.',
                },
              ],
            },
            {
              app: 'hiddify',
              tag: 'Backup',
              hint: 'Added with a button',
              downloads: [
                {
                  store: 'linux',
                  title: 'Download Hiddify for Linux',
                },
              ],
              steps: [
                {
                  title: 'Install Hiddify',
                  text: 'Click the button above — an AppImage file downloads. In the file properties allow running it as a program, then open it.',
                },
                {
                  title: 'Add the subscription',
                  text: 'Go back to the bot, tap «🚀 Подключиться» (Connect), then «Открыть страницу подключения» (Open connection page). There, pick Hiddify and tap «Добавить в Hiddify» (Add to Hiddify) — the app opens and adds the subscription itself.',
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
                  store: 'linux_deb',
                  title: 'For Ubuntu / Debian',
                },
              ],
              steps: [
                {
                  title: 'Install v2rayN',
                  text: 'Click the button above — the package downloads right away — and install it with a double click.',
                },
                {
                  title: 'Add the subscription',
                  text: 'In the bot, tap «📋 Скопировать ссылку» (Copy link) in the «🚀 Подключиться» (Connect) section. In v2rayN, open Subscription group → Subscription group settings → Add, paste the link into the URL field, save and click Update subscription.',
                },
                {
                  title: 'Connect',
                  text: 'Tap the big connect button in v2rayN and allow adding the configuration.',
                  note: 'In v2rayN all traffic goes through the proxy, Russian sites too. If a Russian site does not open, turn the proxy off for a while.',
                },
              ],
            },
            {
              app: 'throne',
              tag: 'Another option',
              hint: 'Added with a button',
              downloads: [
                {
                  store: 'github',
                  title: 'Throne on GitHub',
                },
              ],
              steps: [
                {
                  title: 'Install Throne',
                  text: 'The release page on GitHub opens: download the package for your system (debian-amd64 for Ubuntu and Debian, fedora-amd64 for Fedora) and install it.',
                },
                {
                  title: 'Add the subscription',
                  text: 'Go back to the bot, tap «🚀 Подключиться» (Connect), then «Открыть страницу подключения» (Open connection page). There, pick Throne and tap «Добавить в Throne» (Add to Throne) — the app opens and adds the subscription itself.',
                },
                {
                  title: 'Connect',
                  text: 'Tap the big connect button in Throne and allow adding the configuration.',
                  note: 'In Throne all traffic goes through the proxy, Russian sites too. If a Russian site does not open, turn the proxy off for a while.',
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
              text: 'Happ or OneXray',
              url: '/help/ios/',
            },
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
          ],
        },
      },
    ],
  },
};

export default data;
