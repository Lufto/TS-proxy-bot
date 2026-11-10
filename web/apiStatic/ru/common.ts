import { RES_STATUS } from '@/shared/enums';
import { type TCommonSchema } from '@/app-layer/model/common';

const data: TCommonSchema = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    header: {
      nav: [
        { title: 'Тарифы', url: '/#tariffs' },
        { title: 'Инструкции', url: '/help/' },
      ],
      cta: 'Открыть бота',
      menuOpen: 'Открыть меню',
      menuClose: 'Закрыть меню',
      langLabel: 'Язык сайта',
    },
    footer: {
      tagline: 'Защищённое соединение для телефона, компьютера и планшета.',
      support: 'Написать в поддержку',
      links: [
        { title: 'Инструкции', url: '/help/' },
        { title: 'Пользовательское соглашение', url: '/terms/' },
        { title: 'Политика конфиденциальности', url: '/privacy/' },
      ],
      // Кодовое слово Platega на время согласования кассы — после регистрации убрать
      copyright: 'TS Proxy · Platega test',
    },
  },
};

export default data;
