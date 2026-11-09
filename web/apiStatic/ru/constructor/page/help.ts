import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Список инструкций (задача сайта 09). Ссылки на магазины — на страницах устройств, из config/site.yaml
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'Инструкции по подключению — TS Proxy',
      description: 'Как подключить TS Proxy на iPhone и iPad, Android, Windows и macOS: приложение Happ, шаги, что делать, если не работает.',
    },
    content: [
      {
        type: 'pageHead',
        uuid: 'head',
        data: {
          label: 'Инструкции',
          title: 'Подключение за пару минут',
          text: 'Основное приложение — Happ, запасное — v2RayTun. Сначала возьмите пробный период или оплатите тариф в [боте](bot), потом выберите устройство.',
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
              text: 'Happ из App Store',
              url: '/help/ios/',
            },
            {
              title: 'Android',
              text: 'Happ из Google Play',
              url: '/help/android/',
            },
            {
              title: 'Windows',
              text: 'Happ для Windows',
              url: '/help/windows/',
            },
            {
              title: 'macOS',
              text: 'Happ из Mac App Store',
              url: '/help/macos/',
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
          text: 'Установите v2RayTun и пройдите те же шаги:',
          items: [
            'на iPhone, iPad и Android — на странице подключения выберите v2RayTun вместо Happ;',
            'на Windows и macOS — скопируйте ссылку в боте («📋 Скопировать ссылку») и добавьте её в v2RayTun.',
          ],
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
