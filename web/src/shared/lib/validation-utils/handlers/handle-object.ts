import * as v from 'valibot';
import type { TPath, TRemoval } from '../validation-report';
import { FILTER_OUT, filterDataBySchema } from './_core';

export function handleObject(
  schema: v.ObjectSchema<any, any>,
  data: any,
  endpoint: string,
  path: TPath,
  removals: TRemoval[],
): any {
  if (typeof data !== 'object' || data === null) {
    return FILTER_OUT;
  }

  const result: any = {};
  for (const [key, value] of Object.entries(schema.entries)) {
    const fieldPath = [...path, key];
    if (data[key] !== undefined && data[key] !== null && data[key] !== '') {
      const resFilter = filterDataBySchema(
        value as v.GenericSchema,
        data[key],
        `${endpoint}:${key}`,
        fieldPath,
        removals,
      );
      if (resFilter !== FILTER_OUT) {
        result[key] = resFilter;
      }
      else {
        removals.push({ path: fieldPath, reason: 'поле удалено' });
      }
    }
    else if (key in data && data[key] !== undefined) {
      removals.push({ path: fieldPath, reason: 'поле удалено' });
    }
  }
  return result;
}
