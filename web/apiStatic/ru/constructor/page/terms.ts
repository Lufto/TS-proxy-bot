import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Пользовательское соглашение — TS Proxy',
    },
    content: [
      {
        type: 'stub',
        data: {
          title: 'Пользовательское соглашение',
          description: 'Здесь будет пользовательское соглашение с условиями возврата.',
        },
        uuid: 'stub',
      },
    ],
  },
};

export default data;
