'use client';

import { PopupsEngineProvider, PopupsEngineRoot } from '@idem.agency/popups-engine';
import { useScrollLock } from '@/shared/lib/use-scroll-lock';
import { popups } from '@/app-layer/popups/popups-list';
import '@idem.agency/popups-engine/css';

export const PopupsProvider: FCClass = ({ children }) => {
  const { lock, enable } = useScrollLock();

  return (
    <PopupsEngineProvider popups={popups}>
      {children}

      <PopupsEngineRoot
        id="popups-root"
        lockBodyScroll={lock}
        enableBodyScroll={enable}
      />
    </PopupsEngineProvider>
  );
};
