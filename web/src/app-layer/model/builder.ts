import * as v from 'valibot';
import { blockSchema } from '@/app-layer/config/block-schema';
import { createConstructorPageSchema, createSchemaWithRes } from '@/shared/model/schemas';

type TSchema = v.BaseSchema<unknown, unknown, v.BaseIssue<unknown>>;

type TContentSchema<T extends Record<string, TSchema>> = {
  [K in keyof T & string]: v.ObjectSchema<{
    type: v.LiteralSchema<K, undefined>;
    uuid: v.UnionSchema<readonly [v.StringSchema<undefined>, v.NumberSchema<undefined>], undefined>;
    data: T[K];
  }, undefined>;
}[keyof T & string];

const createContentUnion = <
  T extends Record<string, TSchema>,
>(blocks: T) => {
  const schemas = Object.entries(blocks).map(([type, data]) =>
    v.object({
      type: v.literal(type),
      uuid: v.union([v.string(), v.number()]),
      data,
    }),
  ) as unknown as [
    TContentSchema<T>,
    ...TContentSchema<T>[],
  ];

  return v.variant('type', schemas);
};

const contentSchemas = createContentUnion(blockSchema);

export const builderPageSchema = createConstructorPageSchema(contentSchemas);
export const previewPageSchema = createSchemaWithRes(contentSchemas);

export type TBlock = v.InferOutput<typeof contentSchemas>;
export type TBuilderPage = v.InferOutput<typeof builderPageSchema>;
export type TPreviewPage = v.InferOutput<typeof previewPageSchema>;
