"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";

interface RefreshContextValue {
  tick: number;
  bump: () => void;
}

const RefreshContext = createContext<RefreshContextValue>({ tick: 0, bump: () => {} });

export function RefreshProvider({ children }: { children: ReactNode }) {
  const [tick, setTick] = useState(0);
  const bump = useCallback(() => setTick((t) => t + 1), []);
  const value = useMemo(() => ({ tick, bump }), [tick, bump]);
  return <RefreshContext.Provider value={value}>{children}</RefreshContext.Provider>;
}

export function useRefreshTick(): number {
  return useContext(RefreshContext).tick;
}

export function useRefreshBump(): () => void {
  return useContext(RefreshContext).bump;
}
