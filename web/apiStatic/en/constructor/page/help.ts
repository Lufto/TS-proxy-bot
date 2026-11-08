import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Setup guides — TS Proxy',
    },
    content: [
      {
        type: 'stub',
        data: {
          title: 'Setup guides',
          description: 'Setup guides for each device will be here.',
        },
        uuid: 'stub',
      },
    ],
  },
};

export default data;
