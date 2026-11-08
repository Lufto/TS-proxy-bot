import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Политика конфиденциальности — TS Proxy',
    },
    content: [
      {
        type: 'stub',
        data: {
          title: 'Политика конфиденциальности',
          description: 'Здесь будет политика конфиденциальности.',
        },
        uuid: 'stub',
      },
    ],
  },
};

export default data;
