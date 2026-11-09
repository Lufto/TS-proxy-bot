import { AppSteps } from '@/widgets/app-steps';
import { Cta } from '@/widgets/cta';
import { Devices } from '@/widgets/devices';
import { Document } from '@/widgets/document';
import { Features } from '@/widgets/features';
import { Hero } from '@/widgets/hero';
import { PageHead } from '@/widgets/page-head';
import { Steps } from '@/widgets/steps';
import { Tariffs } from '@/widgets/tariffs';
import { TextList } from '@/widgets/text-list';

export const blockComponent = {
  hero: Hero,
  features: Features,
  steps: Steps,
  tariffs: Tariffs,
  devices: Devices,
  textList: TextList,
  cta: Cta,
  pageHead: PageHead,
  document: Document,
  appSteps: AppSteps,
} as const;
