import * as v from 'valibot';

export const ctaSchema = v.object({
  title: v.string(),
  text: v.optional(v.string()),
  buttons: v.array(v.object({
    title: v.string(),
    url: v.string(),
    variant: v.optional(v.picklist(['primary', 'secondary', 'ghost'])),
  })),
});

export type TCta = v.InferOutput<typeof ctaSchema>;
