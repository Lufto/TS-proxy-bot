'use client';

import { lazy } from 'react';

export const popups = {
  message: lazy(
    () => import('@/widgets/popups/message'),
  ),
} as const;
