import * as v from 'valibot';

export const devicesSchema = v.object({
  id: v.optional(v.string()),
  label: v.optional(v.string()),
  title: v.string(),
  text: v.optional(v.string()),
  items: v.array(v.object({ title: v.string(), text: v.string(), url: v.string() })),
});

export type TDevices = v.InferOutput<typeof devicesSchema>;
