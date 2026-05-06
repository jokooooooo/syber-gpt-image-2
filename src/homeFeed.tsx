import { createContext, useCallback, useContext, useMemo, useState } from 'react';
import type { Dispatch, ReactNode, SetStateAction } from 'react';
import type { HistoryItem, InspirationItem } from './api';

export type HomeFeedState = {
  history: HistoryItem[];
  inspirations: InspirationItem[];
  feedLoading: boolean;
  loadingMoreFeed: boolean;
  hasMoreInspirations: boolean;
  inspirationOffset: number;
  inspirationTotal: number;
  inspirationSearchInput: string;
  inspirationQuery: string;
  inspirationSearchMode: 'keyword' | 'ai';
  inspirationAIQuery: string;
  loadedOwnerId: string;
  loadedQuery: string;
  loadedSearchMode: 'keyword' | 'ai';
  initialized: boolean;
  scrollY: number;
};

type HomeFeedContextValue = {
  state: HomeFeedState;
  setState: Dispatch<SetStateAction<HomeFeedState>>;
  patchState: (patch: Partial<HomeFeedState>) => void;
};

const initialState: HomeFeedState = {
  history: [],
  inspirations: [],
  feedLoading: true,
  loadingMoreFeed: false,
  hasMoreInspirations: true,
  inspirationOffset: 0,
  inspirationTotal: 0,
  inspirationSearchInput: '',
  inspirationQuery: '',
  inspirationSearchMode: 'keyword',
  inspirationAIQuery: '',
  loadedOwnerId: '',
  loadedQuery: '',
  loadedSearchMode: 'keyword',
  initialized: false,
  scrollY: 0,
};

const HomeFeedContext = createContext<HomeFeedContextValue | null>(null);

export function HomeFeedProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<HomeFeedState>(initialState);
  const patchState = useCallback(
    (patch: Partial<HomeFeedState>) => setState((current) => ({ ...current, ...patch })),
    [],
  );

  const value = useMemo(
    () => ({
      state,
      setState,
      patchState,
    }),
    [patchState, state],
  );

  return <HomeFeedContext.Provider value={value}>{children}</HomeFeedContext.Provider>;
}

export function useHomeFeed() {
  const context = useContext(HomeFeedContext);
  if (!context) {
    throw new Error('useHomeFeed must be used inside HomeFeedProvider');
  }
  return context;
}
