import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Перевод русской редакции (apiStatic/ru/...). Меняете русский текст — поправьте и этот, и дату
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Terms of Service — TS Proxy',
      description: 'Terms of use of the TS Proxy service: payment, refunds and liability.',
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
              title: '1. General provisions',
              items: [
                {
                  numbered: [
                    'These Terms of Service (the “Terms”) set out the conditions for using the TS Proxy service (the “Service”), access to which is provided through the Service’s Telegram bot. The Service administration is referred to as the “Administration”, and a person using the Service as the “User”.',
                    'Starting to use the Service, including the free trial, or paying for any plan constitutes the User’s full and unconditional acceptance of these Terms and the [Privacy Policy](/privacy/).',
                    'The Service is independent and is not affiliated with Telegram. Telegram is not a party to these Terms, bears no obligations under them, and Telegram support does not handle requests concerning the Service or payments made in it.',
                    'The Administration may amend these Terms unilaterally. A new version takes effect upon its publication on this page.',
                  ],
                },
              ],
            },
            {
              title: '2. Subject matter',
              items: [
                {
                  numbered: [
                    'The Administration provides the User with access to the Service for the paid period, and the User pays for access at the current rates.',
                    'Plans, the scope of services and the terms of the free trial are published in the Telegram bot and on the Service’s website and may be changed by the Administration. Changes do not apply to a period already paid for.',
                    'Using the Service requires third-party software. The Administration is not responsible for its operation, availability or compatibility with the User’s device.',
                    'The Administration does not guarantee access to any particular websites, applications or services.',
                  ],
                },
              ],
            },
            {
              title: '3. Payment',
              items: [
                {
                  numbered: [
                    'Payment is made by the methods available in the Service’s Telegram bot. The User’s payment obligation is fulfilled once the payment is confirmed by the payment service or Telegram.',
                    'No automatic charges are made. The access period is extended only when the User pays for a plan.',
                    'Fees and terms of banks, payment services and Telegram are set by those parties and do not depend on the Administration.',
                  ],
                },
              ],
            },
            {
              title: '4. Refunds',
              items: [
                {
                  numbered: [
                    'Refunds are made only in the following cases: (a) a repeated payment for the same order; (b) a request from the User received within 14 (fourteen) calendar days from the date the User started using the Service. The free trial period counts towards these 14 days.',
                    'In all other cases, including when the Service is not used and when access is terminated under section 5, payments are not refunded.',
                    'Refunds are made by the same method as the payment. The access period granted by a refunded payment is cancelled.',
                    'Requests about payments and refunds are sent to the Service’s support: in the Telegram bot (including the /paysupport command) or at {email}.',
                    'If a payment is disputed through a bank or payment service, the Administration may terminate the User’s access to the Service.',
                  ],
                },
              ],
            },
            {
              title: '5. User obligations',
              items: [
                {
                  numbered: [
                    'The User is solely responsible for their actions when using the Service and for their compliance with applicable law.',
                    'Using the Service for unlawful purposes, spam, attacks on networks and devices, distribution of malicious software, or actions that infringe the rights of third parties is prohibited.',
                    'Access to the Service is provided for personal use. Transferring access to third parties, reselling it or any other commercial use is prohibited.',
                    'If this section is breached, the Administration may suspend or terminate the User’s access to the Service without notice and without a refund.',
                  ],
                },
              ],
            },
            {
              title: '6. Limitation of liability',
              items: [
                {
                  numbered: [
                    'The Service is provided “as is”. The Administration does not guarantee that the Service will be uninterrupted, always available or meet the User’s expectations.',
                    'The Administration is not responsible for the actions and decisions of third parties, including telecom operators, payment services, Telegram and software developers, or for restrictions imposed by them or by public authorities.',
                    'The Administration is not liable for any direct or indirect losses of the User related to the use of the Service or the inability to use it. In any case, the Administration’s liability is limited to the amount of the User’s last payment.',
                  ],
                },
              ],
            },
            {
              title: '7. Final provisions',
              items: [
                {
                  numbered: [
                    'The Administration may change or discontinue the Service.',
                    'Requests to the Administration are sent to support in the Service’s Telegram bot or to {email}.',
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
