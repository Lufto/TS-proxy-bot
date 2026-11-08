import * as v from 'valibot';
import type { TPath, TRemoval } from '../validation-report';
import { FILTER_OUT, filterDataBySchema } from './_core';

export function handleUnion(
  schema: v.UnionSchema<any, any>,
  data: any,
  endpoint: string,
  path: TPath,
  removals: TRemoval[],
): any {
  for (const option of schema.options as v.GenericSchema[]) {
    const optionRemovals: TRemoval[] = [];
    const result = filterDataBySchema(option, data, endpoint, path, optionRemovals);
    if (v.safeParse(option, result).success) {
      removals.push(...optionRemovals);
      return result;
    }
  }
  return FILTER_OUT;
}
