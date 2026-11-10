import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Перевод русской редакции (apiStatic/ru/...). Меняете русский текст — поправьте и этот, и дату
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Privacy Policy — TS Proxy',
      description: 'How the TS Proxy service processes user data.',
    },
    content: [
      {
        type: 'document',
        uuid: 'privacy',
        data: {
          title: 'Privacy Policy',
          edition: 'Version of 10 November 2026',
          note: 'This is a translation. If the English and Russian versions differ, the Russian version applies.',
          sections: [
            {
              title: '1. General provisions',
              items: [
                {
                  numbered: [
                    'This Privacy Policy (the “Policy”) sets out how the TS Proxy service (the “Service”) processes user data. Terms are used as defined in the [Terms of Service](/terms/).',
                    'Using the Service means the User agrees to this Policy.',
                    'The Service is not affiliated with Telegram. Telegram processes data under its own rules.',
                  ],
                },
              ],
            },
            {
              title: '2. Data processed',
              items: [
                {
                  numbered: [
                    'The Administration stores only the data needed for the Service to work and does not collect Users’ personal data.',
                    'The data needed for the Service to work is: the Telegram ID and username, order and payment details, the content of support requests, and technical information about connections to the Service. Data is received from Telegram and payment services when the Service is used, and from the User when contacting support.',
                    'The Service’s website does not use cookies or web analytics.',
                  ],
                },
              ],
            },
            {
              title: '3. Purposes of processing',
              items: [
                'Data is processed to provide access to the Service, handle payments and refunds, run support, send notifications related to the Service and prevent abuse.',
              ],
            },
            {
              title: '4. Data sharing',
              items: [
                'The Administration does not sell or share data with third parties, except with payment services to the extent needed to process a payment, and where required by law.',
              ],
            },
            {
              title: '5. Storage and protection',
              items: [
                'Data is kept for as long as needed for the purposes of processing. The Administration takes reasonable measures to protect data from unauthorised access.',
              ],
            },
            {
              title: '6. User rights',
              items: [
                'The User may request information about their data by contacting support in the Service’s Telegram bot or at {email}.',
              ],
            },
            {
              title: '7. Changes to the Policy',
              items: [
                'The Administration may amend this Policy unilaterally. A new version takes effect upon its publication on this page.',
              ],
            },
          ],
        },
      },
    ],
  },
};

export default data;
