import { RES_STATUS } from '@/shared/enums';
import { TBuilderPage } from '@/app-layer/model/builder';

// Инструкция: iPhone и iPad. Скриншоты шагов — поле image у шага (src, width, height, alt), когда владелец пришлёт
const data: TBuilderPage = {
  status: RES_STATUS.success,
  statusCode: 200,
  data: {
    seo: {
      title: 'iPhone и iPad — Инструкция — TS Proxy',
      description: 'Как подключить TS Proxy на iPhone и iPad через приложение Happ.',
    },
    content: [
      {
        type: 'appSteps',
        uuid: 'steps',
        data: {
          label: 'Инструкция',
          title: 'iPhone и iPad',
          text: 'Три шага через приложение Happ. Если оно не ставится — есть [запасное приложение](/help/#fallback).',
          downloads: [
            {
              app: 'happ',
              store: 'ios',
              title: 'Happ в App Store',
            },
            {
              app: 'v2raytun',
              store: 'ios',
              title: 'v2RayTun — запасное',
            },
          ],
          steps: [
            {
              title: 'Установите Happ',
              text: 'Установите приложение Happ из App Store — кнопка выше.',
              note: 'Не находится в App Store? В российском App Store его может не быть. Смените страну Apple ID на зарубежную: «Настройки» → ваше имя → «Медиаматериалы и покупки» → «Просмотреть» → «Страна или регион» (способ оплаты — «Нет»). После этого найдите Happ и установите.',
            },
            {
              title: 'Добавьте подписку',
              text: 'Вернитесь в бота, нажмите «🚀 Подключиться», затем «Открыть страницу подключения» и на ней — кнопку добавления в Happ.',
            },
            {
              title: 'Подключитесь',
              text: 'В Happ нажмите большую кнопку подключения и разрешите добавить конфигурацию.',
            },
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
      {
        type: 'devices',
        uuid: 'other',
        data: {
          title: 'Все устройства',
          items: [
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
    ],
  },
};

export default data;
