import * as v from 'valibot';

export type TTwitterCardType = 'summary' | 'summary_large_image' | 'player' | 'app';

export const seoSchema = v.partial(v.object({
  title: v.string(),
  description: v.string(),
  keywords: v.string(),
  robots: v.string(),
  image: v.string(),
  type: v.string(),
  card: v.string(),
}));

export type TSeoSchema = v.InferOutput<typeof seoSchema>;
