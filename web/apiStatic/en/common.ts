import { RES_STATUS } from '@/shared/enums';
import { type TCommonSchema } from '@/app-layer/model/common';

const data: TCommonSchema = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    header: {
      title: 'TS Proxy',
    },
    footer: {
      title: '© TS Proxy',
    },
  },
};

export default data;
