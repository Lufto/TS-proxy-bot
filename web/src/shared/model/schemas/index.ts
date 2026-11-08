import * as v from 'valibot';
import { RES_STATUS } from '@/shared/enums';
import { seoSchema } from './seo';

export const resSchema = v.object({
  status: v.enum(RES_STATUS),
  errors: v.optional(v.any()),
  statusCode: v.optional(v.number()),
});

export const breadcrumbsItem = v.object({
  title: v.string(),
  url: v.optional(v.string()),
});

export const createConstructorPageSchema = <T extends v.GenericSchema>(contentSchema: T) => {
  return v.object({
    ...resSchema.entries,
    data: v.optional(v.object({
      seo: v.optional(seoSchema),
      breadcrumbs: v.optional(v.array(breadcrumbsItem)),
      content: v.array(contentSchema),
    })),
  });
};

export const createSchemaWithRes = <T extends v.GenericSchema>(contentSchema: T) => {
  return v.object({
    ...resSchema.entries,
    data: v.optional(v.intersect([
      v.object({
        seo: v.optional(seoSchema),
      }),
      contentSchema,
    ])),
  });
};

export const createSchemaWithOnlyRes = <T extends v.GenericSchema>(contentSchema: T) => {
  return v.object({
    ...resSchema.entries,
    data: v.optional(contentSchema),
  });
};
