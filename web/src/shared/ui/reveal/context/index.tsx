'use client';

import { createContext, useContext, useMemo } from 'react';
import { TRevealType } from '../types';

const RevealContext = createContext<{ type: TRevealType } | undefined>(undefined);

export const RevealProvider: FCClass<{ type: TRevealType }> = ({
  type,
  children,
}) => {
  const value = useMemo(() => {
    return {
      type,
    };
  }, [type]);

  return (
    <RevealContext.Provider value={value}>
      {children}
    </RevealContext.Provider>
  );
};

export function useRevealProvider() {
  const context = useContext(RevealContext);
  if (!context) {
    throw new Error('useRevealProvider не в RevealProvider');
  }
  return context;
};
