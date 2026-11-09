import * as v from 'valibot';

// Цены и сроки здесь не задаются: они из общего конфига (config/tariffs.yaml)
export const tariffsSchema = v.object({
  id: v.optional(v.string()),
  label: v.optional(v.string()),
  title: v.string(),
  text: v.optional(v.string()),
  perMonth: v.string(),
  discount: v.string(),
  days: v.string(),
  best: v.string(),
  button: v.object({ title: v.string(), url: v.string() }),
  notes: v.optional(v.array(v.object({ title: v.string(), text: v.string() }))),
});

export type TTariffs = v.InferOutput<typeof tariffsSchema>;
