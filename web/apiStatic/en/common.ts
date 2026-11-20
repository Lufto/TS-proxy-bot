import { RES_STATUS } from '@/shared/enums';
import { type TCommonSchema } from '@/app-layer/model/common';

const data: TCommonSchema = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    header: {
      nav: [
        { title: 'Pricing', url: '/#tariffs' },
        { title: 'Guides', url: '/help/' },
      ],
      cta: 'Open the bot',
      menuOpen: 'Open menu',
      menuClose: 'Close menu',
      langLabel: 'Site language',
    },
    footer: {
      tagline: 'A secure connection for your phone, computer and tablet.',
      support: 'Contact support',
      links: [
        { title: 'Guides', url: '/help/' },
        { title: 'Terms of Service', url: '/terms/' },
        { title: 'Privacy Policy', url: '/privacy/' },
      ],
      copyright: 'TS Proxy',
    },
  },
};

export default data;
