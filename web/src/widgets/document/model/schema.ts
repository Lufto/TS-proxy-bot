import * as v from 'valibot';

// Абзац — строка; список — { list: [...] }; пункты с номерами «3.1», «3.2» — { numbered: [...] }.
// Разметка строк — как в RichText
const paragraphSchema = v.union([
  v.string(),
  v.object({ list: v.array(v.string()) }),
  v.object({ numbered: v.array(v.string()) }),
]);

export const documentSchema = v.object({
  title: v.string(),
  edition: v.string(),
  note: v.optional(v.string()),
  sections: v.array(v.object({
    title: v.string(),
    items: v.array(paragraphSchema),
  })),
});

export type TDocument = v.InferOutput<typeof documentSchema>;
