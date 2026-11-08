import * as v from 'valibot';
import type { TPath, TRemoval } from '../validation-report';
import { FILTER_OUT, filterDataBySchema } from './_core';

export function handleIntersection(
  schema: v.IntersectSchema<any, any>,
  data: any,
  endpoint: string,
  path: TPath,
  removals: TRemoval[],
): any {
  const [leftSchema, rightSchema] = schema.options;
  const left = filterDataBySchema(leftSchema, data, endpoint, path, removals);
  const right = filterDataBySchema(rightSchema, data, endpoint, path, removals);
  return (left !== FILTER_OUT && right !== FILTER_OUT)
    ? { ...(left ?? {}), ...(right ?? {}) }
    : (left !== FILTER_OUT ? left : right);
}
