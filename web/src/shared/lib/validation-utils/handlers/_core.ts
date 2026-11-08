import * as v from 'valibot';
import type { TPath, TRemoval } from '../validation-report';

export const FILTER_OUT = Symbol('FILTER_OUT');

export type SchemaHandler = (
  schema: any,
  data: any,
  endpoint: string,
  path: TPath,
  removals: TRemoval[],
) => any;

export const schemaHandlers = new Map<string, SchemaHandler>();

export function filterDataBySchema(
  schema: v.GenericSchema,
  data: any,
  endpoint: string,
  path: TPath = [],
  removals: TRemoval[] = [],
): any {
  const handler = schemaHandlers.get(schema.type);
  return handler ? handler(schema, data, endpoint, path, removals) : data;
}
