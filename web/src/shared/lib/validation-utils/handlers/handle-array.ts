import * as v from 'valibot';
import type { TPath, TRemoval } from '../validation-report';
import { FILTER_OUT, filterDataBySchema } from './_core';

export function handleArray(
  schema: v.ArraySchema<any, any>,
  data: any,
  endpoint: string,
  path: TPath,
  removals: TRemoval[],
): any {
  if (!Array.isArray(data)) {
    return FILTER_OUT;
  }
  return data.map((i, key) => {
    const itemPath = [...path, key];
    let tmp = filterDataBySchema(schema.item, i, `${endpoint}:${key}`, itemPath, removals);
    const validate = v.safeParse(schema.item, tmp);
    if (!validate.success) {
      removals.push({ path: itemPath, reason: 'элемент массива удалён' });
      tmp = FILTER_OUT;
    }
    return tmp;
  }).filter(i => i !== FILTER_OUT);
}
