import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'TS Proxy — прокси с шифрованием трафика',
    },
    content: [
      {
        type: 'stub',
        data: {
          title: 'Главная',
          description: 'Здесь будет главная: что такое TS Proxy, тарифы и кнопка «Открыть бота».',
        },
        uuid: 'stub',
      },
    ],
  },
};

export default data;
