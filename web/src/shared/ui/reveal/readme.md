# Анимация появления

Из хранилища компонентов (`component-storage/components/reveal/next-motion`, исходно проект rnr). Перенесён без изменений.

Обёртки на `motion/react`: блок проявляется, когда попадает во вьюпорт. Экспорты — `Reveal` и `RevealItem`.

## Использование

```tsx
<Reveal
  type="fadeUp"
  tag="section"
  className="..."
>
  {children}
</Reveal>
```

С поочерёдным появлением детей:

```tsx
<Reveal
  type="fade"
  tag="ul"
  staggerChildren={0.1}
>
  {items.map((item) => {
    return (
      <RevealItem
        key={item.id}
        tag="li"
      >
        {item.title}
      </RevealItem>
    );
  })}
</Reveal>
```

## Пропсы

### `Reveal`

| Проп | Тип | Дефолт | Описание |
|---|---|---|---|
| `type` | `fade` \| `fadeUp` \| `scale` | `fadeUp` | Вариант анимации; раздаётся детям через контекст |
| `tag` | ключ `motionComponents` | `div` | Какой тег рендерить (`div`, `span`, `p`, `ul`, `li`, `h1`–`h3`, `section`, `nav`) |
| `staggerChildren` | `number` | — | Задержка между детьми, сек. Задан — родитель анимирует только `opacity`, а `type` отрабатывают дети |
| `className` | `string` | — | |

### `RevealItem`

| Проп | Тип | Дефолт | Описание |
|---|---|---|---|
| `tag` | ключ `motionComponents` | `div` | Тег элемента |
| `className` | `string` | — | |

Тип анимации не принимает — берёт из `useRevealProvider()`. Вне `Reveal` бросает ошибку.

## Как выбирается стратегия

На маунте родитель меряет `getBoundingClientRect()` и решает:

- элемент во вьюпорте → `inView` через `requestAnimationFrame` (анимация проигрывается);
- элемент выше вьюпорта (`top < 0`) → `remove`: конечное состояние с `duration: 0`, без анимации. Так блоки не «мигают» при восстановлении скролла;
- элемент ниже вьюпорта → `IntersectionObserver` с `threshold: 0.1`, после срабатывания `disconnect()`.

Анимация одноразовая: обратно в `initial` элемент не возвращается.

## Структура

```
next-motion/
├── ui/                  # Reveal, RevealItem
├── variants/            # fade, fadeUp, scale
├── motion-components/   # белый список motion-тегов
├── context/             # RevealProvider, useRevealProvider
├── const/               # DEF_ANIMATION_DURATION = 0.8
└── types/
```

## Зависимости

- `motion` (`motion/react`)
- `FCClass` — глобальный тип проекта-донора (`props` + `className` + `children`). В своём проекте либо объявить его, либо заменить на обычный `FC<Props & { className?: string }>`.
