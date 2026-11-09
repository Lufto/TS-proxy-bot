import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Главная на английском — перевод apiStatic/ru/constructor/page/index.ts.
// Названия кнопок бота даны по-русски: бот пока только на русском
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'TS Proxy — proxy with traffic encryption',
      description: 'Encrypts your traffic and hides your IP address on public Wi-Fi. Up to 3 devices, unlimited traffic, 7 days free. Setup and payment are in the Telegram bot.',
    },
    content: [
      {
        type: 'hero',
        uuid: 'hero',
        data: {
          label: 'Proxy with traffic encryption',
          title: 'Your traffic is yours alone',
          text: 'TS Proxy encrypts your connection, hides your IP address and protects your data on public Wi-Fi — in cafés, airports and hotels. Setup, payment and support are in the Telegram bot.',
          primary: { title: 'Open the bot', url: 'bot' },
          secondary: { title: 'How to connect', url: '/help/' },
          facts: [
            { value: 'up to 3 devices', label: 'on one subscription' },
            { value: 'unlimited', label: 'traffic on any paid plan' },
            { value: '7 days free', label: 'trial without payment or card' },
          ],
        },
      },
      {
        type: 'features',
        uuid: 'features',
        data: {
          label: 'What you get',
          title: 'Everything in one subscription',
          items: [
            { title: 'Up to 3 devices', text: 'One subscription for your phone, computer and tablet. Devices can be changed in the bot.' },
            { title: 'Unlimited traffic', text: 'On any paid plan — no data caps.' },
            { title: 'All popular systems', text: 'iPhone and iPad, Android, Windows and macOS — via the Happ app.' },
            { title: 'Free trial', text: 'New customers get 7 days and 7 GB of traffic free, without payment or a card.' },
            { title: 'Pay with SBP or Stars', text: 'Through the Russian Faster Payments System or Telegram Stars — right in the bot.' },
            { title: 'Support in the bot', text: 'Ask a question in the chat with the bot and we reply there. Email works too: {email}.' },
          ],
        },
      },
      {
        type: 'steps',
        uuid: 'start',
        data: {
          label: 'Getting started',
          title: 'Three steps to connect',
          items: [
            { title: 'Open the bot', text: 'Tap «🎁 Попробовать бесплатно» (Try for free) or choose a plan in the [Telegram bot](bot).' },
            { title: 'Install Happ', text: 'Links and steps for your device are in the [setup guides](/help/).' },
            { title: 'Connect', text: 'In the bot, tap «🚀 Подключиться» (Connect) — the subscription is added to the app automatically. Then tap the connect button.' },
          ],
        },
      },
      {
        type: 'tariffs',
        uuid: 'tariffs',
        data: {
          id: 'tariffs',
          label: 'Pricing',
          title: 'The longer the plan, the better the price',
          text: 'Every plan includes up to 3 devices and unlimited traffic. Prices are in roubles.',
          perMonth: 'per month',
          discount: 'save',
          days: 'days',
          best: 'Best value',
          button: { title: 'Choose a plan in the bot', url: 'bot' },
          notes: [
            { title: 'Payment', text: 'SBP — price in roubles, paid on the payment service’s page. Telegram Stars — the bot shows the price in Stars before you pay.' },
            { title: 'No automatic charges', text: 'The subscription is extended only when you pay for a plan yourself. New days are added to the end of your current subscription — no need to set anything up again.' },
            { title: 'Free trial', text: '7 days and 7 GB of traffic free, up to 3 devices. Once per Telegram account, with the «🎁 Попробовать бесплатно» button in the bot.' },
            { title: 'Refunds', text: 'You can get a refund within 30 days of payment — contact support. Details are in the [Terms of Service](/terms/).' },
          ],
        },
      },
      {
        type: 'devices',
        uuid: 'devices',
        data: {
          label: 'Devices',
          title: 'Works on your devices',
          text: 'Step-by-step guides — connecting takes a couple of minutes.',
          items: [
            { title: 'iPhone and iPad', text: 'Happ from the App Store', url: '/help/ios/' },
            { title: 'Android', text: 'Happ from Google Play', url: '/help/android/' },
            { title: 'Windows', text: 'Happ for Windows', url: '/help/windows/' },
            { title: 'macOS', text: 'Happ from the Mac App Store', url: '/help/macos/' },
          ],
        },
      },
      {
        type: 'cta',
        uuid: 'cta',
        data: {
          title: 'Try it free for 7\u00A0days',
          text: 'No payment, no card. If you have questions, contact support and we will reply in the bot.',
          buttons: [
            { title: 'Open the bot', url: 'bot' },
            { title: 'Contact support', url: 'support' },
          ],
        },
      },
    ],
  },
};

export default data;
