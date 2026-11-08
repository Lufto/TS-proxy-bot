import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Инструкции по подключению — TS Proxy',
    },
    content: [
      {
        type: 'stub',
        data: {
          title: 'Инструкции',
          description: 'Здесь будут инструкции по подключению для каждого устройства.',
        },
        uuid: 'stub',
      },
    ],
  },
};

export default data;
