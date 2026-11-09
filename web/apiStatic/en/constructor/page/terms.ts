import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Перевод русской редакции (apiStatic/ru/...). Меняете русский текст — поправьте и этот, и дату
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Terms of Service — TS Proxy',
      description: 'Terms of use of TS Proxy: payment, refunds and rules.',
    },
    content: [
      {
        type: 'document',
        uuid: 'terms',
        data: {
          title: 'Terms of Service',
          edition: 'Version of 9 November 2026',
          note: 'This is a translation. If the English and Russian versions differ, the Russian version applies.',
          sections: [
            {
              title: '1. General',
              items: [
                {
                  numbered: [
                    'These terms set out the conditions for using TS Proxy (the “Service”): a proxy service with traffic encryption that works through the Service’s Telegram bot and the website ts-proxy.com. “We” means the Service administration, “you” means the user of the Service.',
                    'By starting to use the Service — including the free trial — or by paying for a plan, you confirm that you have read and accepted these terms and the [Privacy Policy](/privacy/). If you do not agree with them, do not use the Service.',
                    'We may change these terms. A new version is published on this page with its date and takes effect on publication. By continuing to use the Service after the changes, you accept the new version.',
                  ],
                },
              ],
            },
            {
              title: '2. What the Service provides',
              items: [
                {
                  numbered: [
                    'The Service gives access to proxy servers for the paid period via a personal subscription link issued by the Telegram bot.',
                    'One subscription can be used on up to 3 devices. Traffic on a paid subscription is unlimited.',
                    'Connecting requires third-party apps (for example, Happ and v2RayTun). We do not develop them and are not responsible for how they work, their availability in app stores or their compatibility with your device.',
                    'A new user can get a free trial with a time and traffic limit once per Telegram account. We may change the trial conditions or temporarily disable it.',
                    'We do not promise access to any particular websites, apps or services.',
                    'The subscription link is your personal access. Do not share it with other people and do not resell access.',
                  ],
                },
              ],
            },
            {
              title: '3. Payment and subscription period',
              items: [
                {
                  numbered: [
                    'Current plans and prices are shown in the Telegram bot and on the [website](/#tariffs). Payment is made through the Russian Faster Payments System (SBP) on the payment service’s page or with Telegram Stars.',
                    'A subscription is considered paid when the payment service or Telegram has confirmed the payment. After that, the bot extends the subscription and sends you a message.',
                    'If your subscription is still active, the paid days are added to its end; if not, the period starts from the moment of payment.',
                    'There are no automatic charges: the subscription is extended only when you pay for a plan yourself.',
                    'We may change plans and prices. The period you have already paid for does not change.',
                    'Fees and conditions of your bank, the payment service and Telegram (including the price of buying Stars) do not depend on us.',
                  ],
                },
              ],
            },
            {
              title: '4. Refunds',
              items: [
                {
                  numbered: [
                    'You can ask for a refund of a payment within 30 days of the date of that payment. To do so, contact support: in the Telegram bot or by the email given in section 9.',
                    'The decision on a refund is made by the Service administrator.',
                    'If the administrator approves the refund, the money for that payment is returned the same way you paid: roubles to your account (the time it takes depends on the bank and the payment service), Stars to your Telegram balance. The subscription period is reduced by the period that payment gave.',
                    'Each payment can be refunded only once. No refunds are made after 30 days from the date of payment.',
                    'If you accidentally paid twice, the bot itself will offer to refund the second payment with a button within 7 days. Later — through support, within 30 days of the payment.',
                    'If you disputed a payment through your bank or the payment service and the money was returned to you as a result of that dispute, the subscription period is reduced by the period that payment gave (if no paid period remains, access is disabled). Such a payment cannot be refunded again through support. Please contact support first with any payment questions — it is faster.',
                  ],
                },
              ],
            },
            {
              title: '5. Rules of use',
              items: [
                {
                  numbered: [
                    'You are responsible for making sure your use of the Service complies with the laws that apply to you.',
                    'You may not use the Service for any illegal activity, sending spam, attacks on networks and devices, spreading malware, or actions that violate the rights of other people.',
                    'We do not look at your traffic and do not monitor how you use the Service.',
                    'If we become aware of a breach of these terms — for example, from a complaint, a request or an inquiry by a government authority — we may suspend or terminate your access to the Service. In this case, money for the remaining period is not refunded.',
                  ],
                },
              ],
            },
            {
              title: '6. Service “as is”',
              items: [
                {
                  numbered: [
                    'The Service is provided “as is”. We do not guarantee uninterrupted operation, constant availability, any particular connection speed, or that it will work on any device or in any network.',
                    'The Service may be interrupted by maintenance, hardware and network failures, including without notice.',
                    'We are not responsible for restrictions and blocking by internet providers, telecom operators and government authorities.',
                    'We are not responsible for failures and decisions of third-party services the Service depends on: Telegram, the payment service and banks, app stores, connection apps, hosting providers and telecom operators.',
                    'In case of failures on our side, we may, at our discretion, extend the subscription of affected users.',
                  ],
                },
              ],
            },
            {
              title: '7. Limitation of liability',
              items: [
                {
                  numbered: [
                    'To the extent permitted by law, we are not liable for indirect losses, lost profits, loss of data or any damage related to the use of or inability to use the Service.',
                    'In any case, our liability to you is limited to the amount of your last payment.',
                  ],
                },
              ],
            },
            {
              title: '8. Ending use',
              items: [
                {
                  numbered: [
                    'You may stop using the Service at any time. Money for the paid period is refunded only under the rules of section 4.',
                    'When the subscription ends, access is disabled. We may keep it for a few days so that you have time to renew, but we are not obliged to. To connect again, simply pay for a plan.',
                    'We may change or discontinue the Service.',
                  ],
                },
              ],
            },
            {
              title: '9. Contact us',
              items: [
                {
                  numbered: [
                    'Support is available in the Service’s Telegram bot (the “💬 Поддержка” (Support) button; for payment questions, the /paysupport command) and by email at {email}.',
                    'How we handle your data is described in the [Privacy Policy](/privacy/).',
                  ],
                },
              ],
            },
          ],
        },
      },
    ],
  },
};

export default data;
