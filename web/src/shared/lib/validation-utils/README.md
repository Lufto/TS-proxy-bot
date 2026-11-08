# validation-utils

Утилита для фильтрации и валидации данных API по Valibot-схеме.

## Что делает

1. **Фильтрует** данные по форме схемы (убирает лишние поля, которых нет в схеме)
2. **Валидирует** отфильтрованный результат через `v.safeParse`
3. **Логирует** ошибки через winston, не бросает исключений
4. Возвращает очищенные данные или `false` при провале валидации

## Использование

```ts
import { validationUtils } from '@/shared/lib/validation-utils';

const result = validationUtils(MySchema, rawData, '/api/endpoint');
if (!result) return; // валидация не прошла, ошибки уже залогированы
```

Типичный паттерн в API-модуле:

```ts
const data = await api.get({ endpoint: '/projects' });
return validationUtils(ProjectsSchema, data, '/projects');
```

## Архитектура

```
validation-utils/
  index.ts          — точка входа: validationUtils(), withCustomAttr()
  handlers/
    _core.ts        — Map обработчиков + filterDataBySchema()
    index.ts        — регистрация всех handlers
    handle-object.ts
    handle-optional.ts
    handle-array.ts
    handle-intersection.ts
    handle-discriminated-union.ts
    handle-union.ts
```

`filterDataBySchema` — диспетчер: смотрит на `schema.type`, находит handler в Map и вызывает его. Если handler не найден — возвращает данные как есть (примитивы не нуждаются в фильтрации).

## Добавить новый handler

Пример: `record`.

**1. Создать файл** `handlers/handle-record.ts`:

```ts
import * as v from 'valibot';
import { filterDataBySchema } from './_core';

export function handleRecord(schema: v.RecordSchema<any, any, any>, data: any, endpoint: string): any {
  // фильтруем каждое значение по схеме значения
  return Object.fromEntries(
    Object.entries(data).map(([key, value]) => {
      return [key, filterDataBySchema(schema.value, value, `${endpoint}:${key}`)];
    }),
  );
}
```

**2. Зарегистрировать в** `handlers/index.ts`:

```ts
import { handleRecord } from './handle-record';

schemaHandlers.set('record', handleRecord);
```

Всё. `filterDataBySchema` подхватит handler автоматически при следующем встреченном `record`.

Незарегистрированные обёртки (`record`, `tuple`, `nullable`, `fallback`, `lazy`) данные под собой **не фильтруют** — лишние поля проходят насквозь. Добавлять по мере появления в схемах.

## withCustomAttr

Хелпер для навешивания мета-атрибутов на схему (хранится в `WeakMap`, не мутирует схему):

```ts
const attrMap = new WeakMap();
const schema = withCustomAttr(MySchema, { filterMissing: true }, attrMap);
```
