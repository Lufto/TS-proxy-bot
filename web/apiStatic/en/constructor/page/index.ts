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
          text: 'TS Proxy encrypts your connection, hides your IP address and protects your data on public Wi-Fi — in cafés, airports and hotels.',
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
          label: 'Why TS Proxy',
          title: 'Simple from day one',
          items: [
            { title: 'All in one chat', text: 'Subscription, payment and renewal — in Telegram, with no accounts or passwords.' },
            { title: 'No sign-up', text: 'No email, phone number or password needed: your Telegram is your account.' },
            { title: 'One app everywhere', text: 'The same app and the same setup on your phone, tablet and computer.' },
            { title: 'Timely reminders', text: 'The bot lets you know when your subscription is about to end — renewing takes a couple of taps.' },
            { title: 'Devices under control', text: 'The bot shows what is connected to your subscription — remove a device and add another one.' },
            { title: 'Real people', text: 'Questions are handled by live support, not an autoresponder.' },
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
            { title: 'Open the bot', text: 'Start a free trial or get a subscription in the [Telegram bot](bot).' },
            { title: 'Install the app', text: 'Links for your device and setup steps are in the [guides](/help/).' },
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
            { title: 'Support', text: 'Can’t connect or something went wrong with a payment? Contact support — we will help sort it out.' },
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
            { title: 'iPhone and iPad', text: 'Happ or OneXray', url: '/help/ios/' },
            { title: 'Android', text: 'Happ, v2RayTun and 2 more', url: '/help/android/' },
            { title: 'Windows', text: 'Happ, v2RayTun and 2 more', url: '/help/windows/' },
            { title: 'macOS', text: 'Happ, OneXray and 2 more', url: '/help/macos/' },
            { title: 'Linux', text: 'Happ, Hiddify and 2 more', url: '/help/linux/' },
          ],
        },
      },
      {
        type: 'cta',
        uuid: 'cta',
        data: {
          title: 'Try it free for 7\u00A0days',
          text: 'No payment, no card — just open the bot.',
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
