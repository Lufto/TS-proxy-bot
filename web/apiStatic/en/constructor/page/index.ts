import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'TS Proxy — encrypted proxy service',
    },
    content: [
      {
        type: 'stub',
        data: {
          title: 'Home',
          description: 'The home page will be here: what TS Proxy is, plans and the “Open bot” button.',
        },
        uuid: 'stub',
      },
    ],
  },
};

export default data;
