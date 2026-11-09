import * as v from 'valibot';

export const pageHeadSchema = v.object({
  label: v.optional(v.string()),
  title: v.string(),
  text: v.optional(v.string()),
});

export type TPageHead = v.InferOutput<typeof pageHeadSchema>;
