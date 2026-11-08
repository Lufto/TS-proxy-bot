import * as v from 'valibot';
import { FILTER_OUT, filterDataBySchema } from './handlers';
import { logValidationReport, type TRemoval } from './validation-report';

export const withCustomAttr = <T extends v.GenericSchema>(
  schema: T,
  attr: Record<'filterMissing', boolean>,
  attrMap: WeakMap<v.GenericSchema, any>,
): T => {
  attrMap.set(schema, attr);
  return schema;
};

export const validationUtils = <TSchema extends v.GenericSchema>(
  schema: TSchema,
  data: unknown,
  endPoint: string = '',
): v.InferOutput<TSchema> | false => {
  const enabled = process.env.VALIDATION_LOG_ENABLED === 'true'
    || process.env.VALIDATION_TERMINAL_LOG_ENABLED === 'true';
  const original = v.safeParse(schema, data);
  if (original.success) return original.output;

  const removals: TRemoval[] = [];
  const cleanedData = filterDataBySchema(schema, data, endPoint, [], removals);
  const validate = v.safeParse(schema, cleanedData === FILTER_OUT ? undefined : cleanedData);
  const success = cleanedData !== FILTER_OUT && validate.success;
  if (enabled) {
    logValidationReport(endPoint, original.issues, removals, success);
  }
  return success ? validate.output : false;
};
