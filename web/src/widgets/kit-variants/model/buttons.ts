// Варианты кнопок «Манифеста»: владелец просил переделать «тень-сдвиг» у главной
// и рамку у второй. Сами анимации — в kit.css, классы `kit-btn-anim-<id>` и `kit-btn2-anim-<id>`.
export type TButtonOption = {
  id: string;
  name: string;
  idea: string;
};

export const MANIFEST_BUTTONS: TButtonOption[] = [
  {
    id: '1',
    name: 'Заливка',
    idea: 'Светлая заливка резко пробегает слева направо, стрелка сдвигается.',
  },
  {
    id: '2',
    name: 'Нажатие',
    idea: 'За кнопкой видна оранжевая рамка. При наведении кнопка вдавливается в неё, как физическая клавиша.',
  },
  {
    id: '3',
    name: 'Вылет стрелки',
    idea: 'Стрелка улетает вправо, новая влетает слева. Кнопка чуть светлеет.',
  },
  {
    id: '4',
    name: 'Бегущая строка',
    idea: 'Надпись уезжает вверх, снизу приезжает такая же, как табло на вокзале. Стрелка уходит вправо.',
  },
  {
    id: '5',
    name: 'Негатив',
    idea: 'Кнопка становится прозрачной с оранжевой рамкой, текст — оранжевым.',
  },
];

// Вторая кнопка («Как подключиться»)
export const MANIFEST_SECONDARY_BUTTONS: TButtonOption[] = [
  {
    id: '1',
    name: 'Контур и строка',
    idea: 'Светлая рамка без заливки. При наведении — та же бегущая строка, что у главной, рамка и текст становятся оранжевыми.',
  },
  {
    id: '2',
    name: 'Подъём заливки',
    idea: 'Светлая рамка. При наведении светлая заливка поднимается снизу, текст становится чёрным.',
  },
  {
    id: '3',
    name: 'Черта',
    idea: 'Без рамки: текст на толстой оранжевой черте. При наведении черта вырастает в плашку под всем текстом.',
  },
  {
    id: '4',
    name: 'Светлая плашка',
    idea: 'Сплошная светлая кнопка рядом с оранжевой. При наведении заливка уходит, остаётся светлая рамка.',
  },
  {
    id: '5',
    name: 'Уголки',
    idea: 'Только уголки рамки, как в видоискателе. При наведении они дорастают до полной рамки.',
  },
  {
    id: '6',
    name: 'Плашка и заливка',
    idea: 'Вид светлой плашки (4) и движение подъёма заливки (2): при наведении светлая заливка уходит вниз, остаётся светлая рамка.',
  },
];

// Выбор владельца на 2026-11-09: главная — «Бегущая строка», вторая — сочетание 2 и 4
export const DEFAULT_BUTTON_ID = '4';
export const DEFAULT_SECONDARY_ID = '6';

const findOption = (options: TButtonOption[], id: string, fallbackId: string) =>
  options.find(option => option.id === id) ?? options.find(option => option.id === fallbackId)!;

export const findButtonOption = (buttonId: string) =>
  findOption(MANIFEST_BUTTONS, buttonId, DEFAULT_BUTTON_ID);

export const findSecondaryOption = (buttonId: string) =>
  findOption(MANIFEST_SECONDARY_BUTTONS, buttonId, DEFAULT_SECONDARY_ID);
