import * as v from 'valibot';

const enum PictureVariant {
  def = '',
  contain = 'contain',
}

interface IPicture {
  alt?: string;
  width?: number;
  height?: number;
  fill?: boolean;
  noLazy?: boolean;
  sizes?: string;
}

export const getPictureSchema = () => {
  return v.object({
    desktop: v.optional(v.string()),
    tablet: v.optional(v.string()),
    src: v.string(),
    alt: v.optional(v.string()),
  });
};

export const pictureSchema = getPictureSchema();

type TPicture = IPicture & v.InferOutput<typeof pictureSchema> & { variant?: `${PictureVariant}` };

export type { TPicture };
