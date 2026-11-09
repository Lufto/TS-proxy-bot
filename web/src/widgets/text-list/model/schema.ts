import * as v from 'valibot';

export const textListSchema = v.object({
  id: v.optional(v.string()),
  label: v.optional(v.string()),
  title: v.string(),
  text: v.optional(v.string()),
  items: v.array(v.string()),
  ordered: v.optional(v.boolean()),
  note: v.optional(v.string()),
});

export type TTextList = v.InferOutput<typeof textListSchema>;
