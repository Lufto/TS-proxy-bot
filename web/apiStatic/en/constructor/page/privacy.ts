import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Privacy policy — TS Proxy',
    },
    content: [
      {
        type: 'stub',
        data: {
          title: 'Privacy policy',
          description: 'The privacy policy will be here.',
        },
        uuid: 'stub',
      },
    ],
  },
};

export default data;
