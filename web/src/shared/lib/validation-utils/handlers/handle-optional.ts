import * as v from 'valibot';
import type { TPath, TRemoval } from '../validation-report';
import { FILTER_OUT, filterDataBySchema } from './_core';

export function handleOptional(
  schema: v.OptionalSchema<any, any>,
  data: any,
  endpoint: string,
  path: TPath,
  removals: TRemoval[],
): any {
  if (data !== undefined && data !== null && data !== '') {
    const result = filterDataBySchema(schema.wrapped, data, endpoint, path, removals);
    const validate = v.safeParse(schema.wrapped, result);
    if (!validate.success) {
      return FILTER_OUT;
    }
    return result;
  }
  return FILTER_OUT;
}
