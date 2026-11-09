import * as v from 'valibot';

export const stepsSchema = v.object({
  id: v.optional(v.string()),
  label: v.optional(v.string()),
  title: v.string(),
  text: v.optional(v.string()),
  items: v.array(v.object({ title: v.string(), text: v.string() })),
});

export type TSteps = v.InferOutput<typeof stepsSchema>;
