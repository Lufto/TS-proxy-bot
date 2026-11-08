import * as v from 'valibot';

export const linkSchema = v.partial(v.object({
  title: v.string(),
  url: v.string(),
}));
