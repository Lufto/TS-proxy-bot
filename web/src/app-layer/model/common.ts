import * as v from 'valibot';
import { createSchemaWithOnlyRes } from '@/shared/model/schemas';

export const commonDataSchema = v.partial(v.object({
  header: v.object({
    title: v.string(),
  }),
  footer: v.object({
    title: v.string(),
  }),
}));

export const commonSchema = createSchemaWithOnlyRes(commonDataSchema);

export type TCommonSchema = v.InferOutput<typeof commonSchema>;
