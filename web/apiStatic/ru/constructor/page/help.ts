import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Список инструкций (задача сайта 09). Ссылки на магазины — на страницах устройств, из config/site.yaml
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Инструкции по подключению — TS Proxy',
      description: 'Как подключить TS Proxy на iPhone и iPad, Android, Windows, macOS и Linux: Happ и запасные приложения, шаги, что делать, если не работает.',
    },
    content: [
      {
        type: 'pageHead',
        uuid: 'head',
        data: {
          label: 'Инструкции',
          title: 'Подключение за пару минут',
          text: 'Главное приложение — Happ, на каждом устройстве есть и запасные. Сначала возьмите пробный период или оплатите тариф в [боте](bot), потом выберите устройство.',
        },
      },
      {
        type: 'devices',
        uuid: 'devices',
        data: {
          title: 'Выберите устройство',
          items: [
            {
              title: 'iPhone и iPad',
              text: 'Happ или OneXray',
              url: '/help/ios/',
            },
            {
              title: 'Android',
              text: 'Happ, v2RayTun и ещё 2',
              url: '/help/android/',
            },
            {
              title: 'Windows',
              text: 'Happ, v2RayTun и ещё 2',
              url: '/help/windows/',
            },
            {
              title: 'macOS',
              text: 'Happ, OneXray и ещё 2',
              url: '/help/macos/',
            },
            {
              title: 'Linux',
              text: 'Happ, Hiddify и ещё 2',
              url: '/help/linux/',
            },
          ],
        },
      },
      {
        type: 'textList',
        uuid: 'fallback',
        data: {
          id: 'fallback',
          label: 'Запасное приложение',
          title: 'Если с Happ не получилось',
          text: 'Возьмите запасное приложение для вашего устройства — на странице устройства выберите его, шаги поменяются:',
          items: [
            'iPhone и iPad — OneXray (есть в российском App Store);',
            'Android и Windows — v2RayTun, дальше Hiddify, OneXray или v2rayN;',
            'macOS — OneXray, дальше Hiddify или v2rayN;',
            'Linux — Hiddify, дальше v2rayN или Throne.',
          ],
          note: '«Россия напрямую» — российские сайты без прокси — работает только в Happ и v2RayTun. В остальных приложениях весь трафик идёт через прокси.',
        },
      },
      {
        type: 'textList',
        uuid: 'trouble',
        data: {
          id: 'trouble',
          label: 'Не работает?',
          title: 'Что сделать самому',
          ordered: true,
          items: [
            'Проверьте в боте («👤 Мой профиль»), что подписка активна.',
            'В приложении обновите подписку (кнопка обновления рядом с ней) и подключитесь заново.',
            'Переключите Wi-Fi на мобильный интернет или наоборот.',
            'Перезапустите приложение.',
            'Новое устройство не подключается? В любом тарифе — до 3 устройств. Откройте в боте «👤 Мой профиль» → «📱 Мои устройства» и удалите то, которым больше не пользуетесь.',
          ],
          note: 'Ссылка подписки — ваш личный доступ. Не пересылайте её другим.',
        },
      },
      {
        type: 'cta',
        uuid: 'cta',
        data: {
          title: 'Не помогло?',
          text: 'Напишите в поддержку: какое устройство и приложение, что именно не работает. Скриншот ошибки очень поможет.',
          buttons: [
            {
              title: 'Написать в поддержку',
              url: 'support',
            },
            {
              title: 'Открыть бота',
              url: 'bot',
            },
          ],
        },
      },
    ],
  },
};

export default data;
