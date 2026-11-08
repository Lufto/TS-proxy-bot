import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Terms of service — TS Proxy',
    },
    content: [
      {
        type: 'stub',
        data: {
          title: 'Terms of service',
          description: 'The terms of service with refund conditions will be here.',
        },
        uuid: 'stub',
      },
    ],
  },
};

export default data;
