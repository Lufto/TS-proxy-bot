import * as v from 'valibot';
import type { TPath, TRemoval } from '../validation-report';
import { FILTER_OUT, filterDataBySchema } from './_core';

export function handleDiscriminatedUnion(
  schema: v.VariantSchema<any, any, any>,
  data: any,
  endpoint: string,
  path: TPath,
  removals: TRemoval[],
): any {
  if (typeof data !== 'object' || data === null) {
    return FILTER_OUT;
  }

  const key = schema.key;
  const type = data[key];
  const schemaItem = schema.options.find((option: v.GenericSchema) => {
    const discriminator = (option as v.ObjectSchema<any, any>).entries?.[key];
    return discriminator && v.safeParse(discriminator, type).success;
  });
  if (schemaItem) {
    const result = filterDataBySchema(schemaItem, data, `${endpoint}:${type}`, path, removals);
    const validate = v.safeParse(schemaItem, result);
    if (!validate.success) {
      return FILTER_OUT;
    }
    return result;
  }
  return FILTER_OUT;
}
