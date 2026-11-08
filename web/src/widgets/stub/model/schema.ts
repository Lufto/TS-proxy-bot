import * as v from 'valibot';

export const stubComponentSchema = v.partial(v.object({
  title: v.string(),
  description: v.string(),
}));

export type TStubComponent = v.InferOutput<typeof stubComponentSchema>;
