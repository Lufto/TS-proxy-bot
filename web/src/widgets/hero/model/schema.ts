import * as v from 'valibot';

const linkSchema = v.object({ title: v.string(), url: v.string() });

export const heroSchema = v.object({
  label: v.optional(v.string()),
  title: v.string(),
  text: v.optional(v.string()),
  primary: linkSchema,
  secondary: v.optional(linkSchema),
  facts: v.optional(v.array(v.object({ value: v.string(), label: v.string() }))),
});

export type THero = v.InferOutput<typeof heroSchema>;
