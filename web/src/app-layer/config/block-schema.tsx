import { appStepsSchema } from '@/widgets/app-steps';
import { ctaSchema } from '@/widgets/cta';
import { devicesSchema } from '@/widgets/devices';
import { documentSchema } from '@/widgets/document';
import { featuresSchema } from '@/widgets/features';
import { heroSchema } from '@/widgets/hero';
import { pageHeadSchema } from '@/widgets/page-head';
import { stepsSchema } from '@/widgets/steps';
import { tariffsSchema } from '@/widgets/tariffs';
import { textListSchema } from '@/widgets/text-list';

export const blockSchema = {
  hero: heroSchema,
  features: featuresSchema,
  steps: stepsSchema,
  tariffs: tariffsSchema,
  devices: devicesSchema,
  textList: textListSchema,
  cta: ctaSchema,
  pageHead: pageHeadSchema,
  document: documentSchema,
  appSteps: appStepsSchema,
} as const;
