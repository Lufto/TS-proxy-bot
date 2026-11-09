import * as v from 'valibot';
import { createSchemaWithOnlyRes } from '@/shared/model/schemas';

const navLinkSchema = v.object({
  title: v.string(),
  url: v.string(),
});

export const commonDataSchema = v.partial(v.object({
  header: v.object({
    nav: v.array(navLinkSchema),
    cta: v.string(),
    menuOpen: v.string(),
    menuClose: v.string(),
    langLabel: v.string(),
  }),
  footer: v.object({
    tagline: v.string(),
    cta: v.string(),
    support: v.string(),
    links: v.array(navLinkSchema),
    copyright: v.string(),
  }),
}));

export const commonSchema = createSchemaWithOnlyRes(commonDataSchema);

export type TCommonSchema = v.InferOutput<typeof commonSchema>;
export type TCommonData = v.InferOutput<typeof commonDataSchema>;
export type THeaderData = NonNullable<TCommonData['header']>;
export type TFooterData = NonNullable<TCommonData['footer']>;
