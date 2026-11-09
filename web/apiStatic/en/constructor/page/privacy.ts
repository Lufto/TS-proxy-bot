import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Перевод русской редакции (apiStatic/ru/...). Меняете русский текст — поправьте и этот, и дату
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Privacy Policy — TS Proxy',
      description: 'What data TS Proxy collects, why, and who receives it.',
    },
    content: [
      {
        type: 'document',
        uuid: 'privacy',
        data: {
          title: 'Privacy Policy',
          edition: 'Version of 9 November 2026',
          note: 'This is a translation. If the English and Russian versions differ, the Russian version applies.',
          sections: [
            {
              title: '1. General',
              items: [
                {
                  numbered: [
                    'This policy describes what data the TS Proxy service (the “Service”) collects through the Service’s Telegram bot and the website ts-proxy.com, why, and how we handle it. “We” means the Service administration, “you” means the user of the Service.',
                    'By using the Service, you agree to this policy and the [Terms of Service](/terms/). If you do not agree, do not use the Service.',
                  ],
                },
              ],
            },
            {
              title: '2. What data we collect',
              items: [
                {
                  numbered: [
                    'Telegram data: your Telegram ID, name and @username — as Telegram passes them to the bot.',
                    'Orders and payments: the chosen plan, amount, payment method, time of order and payment, payment number at the payment service or Telegram, refunds.',
                    'Support requests: your messages and files that you send to support through the bot, and our replies. If you write by email — your email address and the content of the message.',
                    'Technical connection data: connected devices (model, operating system, app), connection times, amount of traffic used, and the IP address from which the app updates the subscription.',
                    'Service data: when you first started the bot, whether you received a trial, which notifications the bot sent you, and a log of events for your subscription (orders, renewals, refunds, administrator actions).',
                  ],
                },
                'We do not collect phone numbers or bank card details. Payments are processed by the payment service or Telegram — you enter payment details with them, and they do not reach us. We do not look at the content of your traffic.',
              ],
            },
            {
              title: '3. Website',
              items: [
                'The website ts-proxy.com does not use cookies, visitor counters or advertising trackers. Once a minute the page checks whether the bot is working in order to show a warning about problems; none of your data is sent in the process. Like any website, it may technically receive your IP address and browser information when pages are opened.',
              ],
            },
            {
              title: '4. Why we use data',
              items: [
                {
                  list: [
                    'to grant and extend access to the Service and to show your subscription and connected devices;',
                    'to accept payments, credit them and process refunds;',
                    'to answer support requests;',
                    'to send notifications about your subscription period, payments and the operation of the Service;',
                    'to prevent abuse — for example, getting the trial more than once;',
                    'to resolve payment disputes and confirm that the service was provided;',
                    'to keep the Service running and to find and fix failures.',
                  ],
                },
              ],
            },
            {
              title: '5. Who receives data',
              items: [
                {
                  numbered: [
                    'We do not sell your data and do not share it for advertising.',
                    'The payment service receives the information needed for payment and refunds: amount, order number, purchase description. Telegram receives information about payments in Stars. These services process data under their own rules.',
                    'Messages with the bot go through Telegram and are also subject to Telegram’s rules.',
                    'We may disclose data if required by law.',
                  ],
                },
              ],
            },
            {
              title: '6. Storage and protection',
              items: [
                {
                  numbered: [
                    'We store data as long as it is needed to run the Service, for accounting, resolving payment disputes and meeting legal requirements, including in backups.',
                    'We take reasonable measures to protect data from unauthorized access. It is impossible to completely exclude risks when transmitting and storing data on the internet.',
                  ],
                },
              ],
            },
            {
              title: '7. Your choices',
              items: [
                {
                  numbered: [
                    'You can stop using the Service at any time. If you block the bot in Telegram, it will stop sending you messages.',
                    'Send questions about your data to support: in the Telegram bot or by email at {email}.',
                  ],
                },
              ],
            },
            {
              title: '8. Changes to this policy',
              items: [
                'We may change this policy. A new version is published on this page with its date and takes effect on publication.',
              ],
            },
          ],
        },
      },
    ],
  },
};

export default data;
