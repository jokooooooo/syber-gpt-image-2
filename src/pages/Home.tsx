import { useCallback, useEffect, useRef, useState } from 'react';
import type { ChangeEvent, DragEvent } from 'react';
import { ArrowUp, Heart, ImagePlus, Grid, List, Maximize2, RefreshCw, Loader2, Search, Sparkles, X } from 'lucide-react';
import {
  editImage,
  favoriteInspiration,
  generateImage,
  getConfig,
  getHistory,
  getInspirations,
  HistoryItem,
  InspirationItem,
  optimizePrompt,
  unfavoriteInspiration,
} from '../api';
import { useAuth } from '../auth';
import { copyTextToClipboard } from '../clipboard';
import ImagePreviewModal from '../components/ImagePreviewModal';
import MasonryGrid from '../components/MasonryGrid';
import PromptEditorModal from '../components/PromptEditorModal';
import { useHomeFeed } from '../homeFeed';
import { useSite } from '../site';
import { useTasks } from '../tasks';

const FEED_PAGE_SIZE = 24;
const PROMPT_TRANSFER_KEY = 'joko_pending_prompt';
const SIZE_OPTIONS = ['1K', '2K', '4K'];
const SIZE_LABELS: Record<string, string> = {
  '1K': '1K (1080p)',
  '2K': '2K (1440p)',
  '4K': '4K (2160p)',
};
const ASPECT_RATIO_OPTIONS = ['1:1', '16:9', '9:16', '3:2', '2:3', '4:3', '3:4'];
const QUALITY_OPTIONS = ['auto', 'low', 'medium', 'high'];
const SIZE_PRESETS: Record<string, Record<string, string>> = {
  '1K': {
    '1:1': '1088x1088',
    '16:9': '2048x1152',
    '9:16': '1152x2048',
    '3:2': '1632x1088',
    '2:3': '1088x1632',
    '4:3': '1472x1104',
    '3:4': '1104x1472',
  },
  '2K': {
    '1:1': '1440x1440',
    '16:9': '2560x1440',
    '9:16': '1440x2560',
    '3:2': '2160x1440',
    '2:3': '1440x2160',
    '4:3': '1920x1440',
    '3:4': '1440x1920',
  },
  '4K': {
    '16:9': '3840x2160',
    '9:16': '2160x3840',
    '3:2': '3840x2560',
    '2:3': '2560x3840',
    '4:3': '3840x2880',
    '3:4': '2880x3840',
  },
};
const SIZE_BY_PRESET_VALUE = Object.fromEntries(
  Object.entries(SIZE_PRESETS).flatMap(([scale, ratios]) =>
    Object.values(ratios).map((size) => [size.toUpperCase(), scale]),
  ),
);

function mergeHistory(items: HistoryItem[]) {
  const merged = new Map<string, HistoryItem>();
  for (const item of items) {
    merged.set(item.id, item);
  }
  return [...merged.values()].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
}

type FeedItem = {
  key: string;
  id: string;
  img: string;
  prompt: string;
  title: string;
  inspirationId: string | null;
  favorited: boolean;
};

export default function Home() {
  const { viewer } = useAuth();
  const { t } = useSite();
  const { addTask, openDrawer, taskHistoryItems } = useTasks();
  const { state: feedState, patchState, setState: setFeedState } = useHomeFeed();
  const [promptValue, setPromptValue] = useState('');
  const [promptInstruction, setPromptInstruction] = useState('');
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [selectedPreviews, setSelectedPreviews] = useState<{ id: string; name: string; url: string }[]>([]);
  const [imageScale, setImageScale] = useState('2K');
  const [aspectRatio, setAspectRatio] = useState('1:1');
  const [imageQuality, setImageQuality] = useState('auto');
  const [previewItem, setPreviewItem] = useState<{ imageUrl: string; prompt: string } | null>(null);
  const [promptEditorOpen, setPromptEditorOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [optimizingPrompt, setOptimizingPrompt] = useState(false);
  const [draggingReference, setDraggingReference] = useState(false);
  const [favoritePendingIds, setFavoritePendingIds] = useState<string[]>([]);
  const [showBackToTop, setShowBackToTop] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const loadMoreRef = useRef<HTMLDivElement>(null);
  const {
    history,
    inspirations,
    feedLoading,
    loadingMoreFeed,
    hasMoreInspirations,
    inspirationOffset,
    inspirationTotal,
    inspirationSearchInput,
    inspirationQuery,
  } = feedState;

  useEffect(() => {
    const pendingPrompt = window.sessionStorage.getItem(PROMPT_TRANSFER_KEY);
    if (pendingPrompt) {
      setPromptValue(pendingPrompt);
      window.sessionStorage.removeItem(PROMPT_TRANSFER_KEY);
    }
  }, []);

  useEffect(() => {
    const previews = selectedFiles.map((file, index) => ({
      id: `${file.name}-${file.size}-${file.lastModified}-${index}`,
      name: file.name,
      url: URL.createObjectURL(file),
    }));
    setSelectedPreviews(previews);
    return () => {
      previews.forEach((preview) => URL.revokeObjectURL(preview.url));
    };
  }, [selectedFiles]);

  useEffect(() => {
    let cancelled = false;
    getConfig()
      .then((config) => {
        if (cancelled) return;
        setImageScale(normalizeImageScale(config.default_size));
        setImageQuality(config.default_quality || 'auto');
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [viewer?.owner_id]);

  useEffect(() => {
    if (!isSupportedImagePreset(imageScale, aspectRatio)) {
      setImageScale('2K');
    }
  }, [aspectRatio, imageScale]);

  useEffect(() => {
    const handle = window.setTimeout(() => {
      patchState({ inspirationQuery: inspirationSearchInput.trim() });
    }, 350);
    return () => window.clearTimeout(handle);
  }, [inspirationSearchInput, patchState]);

  useEffect(() => {
    const updateBackToTop = () => setShowBackToTop(window.scrollY > 720);
    updateBackToTop();
    window.addEventListener('scroll', updateBackToTop, { passive: true });
    return () => window.removeEventListener('scroll', updateBackToTop);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const ownerId = viewer?.owner_id || '';
    setError('');
    const query = inspirationQuery || undefined;
    if (feedState.initialized && feedState.loadedOwnerId === ownerId && feedState.loadedQuery === (query || '')) {
      return () => {
        cancelled = true;
      };
    }
    patchState({
      feedLoading: true,
      loadingMoreFeed: false,
      hasMoreInspirations: true,
      inspirationOffset: 0,
      inspirationTotal: 0,
      inspirations: [],
    });
    const task = Promise.all([
      getHistory({ limit: 12 }),
      getInspirations({ limit: FEED_PAGE_SIZE, offset: 0, q: query }),
    ]);
    task
      .then(([historyData, inspirationData]) => {
        if (cancelled) return;
        const nextTotal = Number(inspirationData.total ?? inspirationData.items.length ?? 0);
        patchState({
          history: historyData.items.filter((item) => item.status === 'succeeded' && Boolean(item.image_url)),
          inspirations: inspirationData.items,
          inspirationTotal: nextTotal,
          inspirationOffset: inspirationData.items.length,
          hasMoreInspirations: inspirationData.items.length < nextTotal,
          loadedOwnerId: ownerId,
          loadedQuery: query || '',
          initialized: true,
        });
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message);
        }
      })
      .finally(() => {
        if (!cancelled) {
          patchState({ feedLoading: false });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [feedState.initialized, feedState.loadedOwnerId, feedState.loadedQuery, inspirationQuery, patchState, viewer?.owner_id]);

  const loadMoreInspirations = useCallback(async () => {
    if (feedLoading || loadingMoreFeed || !hasMoreInspirations) {
      return;
    }
    patchState({ loadingMoreFeed: true });
    try {
      const data = await getInspirations({
        limit: FEED_PAGE_SIZE,
        offset: inspirationOffset,
        q: inspirationQuery || undefined,
      });
      setFeedState((current) => {
        const seen = new Set(current.inspirations.map((item) => item.id));
        const nextItems = data.items.filter((item) => !seen.has(item.id));
        const nextOffset = inspirationOffset + data.items.length;
        const nextTotal = Number(data.total ?? inspirationTotal);
        return {
          ...current,
          inspirations: [...current.inspirations, ...nextItems],
          inspirationTotal: nextTotal,
          inspirationOffset: nextOffset,
          hasMoreInspirations: nextTotal > 0 ? nextOffset < nextTotal : data.items.length === FEED_PAGE_SIZE,
        };
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      patchState({ loadingMoreFeed: false });
    }
  }, [feedLoading, hasMoreInspirations, inspirationOffset, inspirationQuery, inspirationTotal, loadingMoreFeed, patchState, setFeedState]);

  useEffect(() => {
    const target = loadMoreRef.current;
    if (!target || feedLoading || loadingMoreFeed || !hasMoreInspirations) {
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          loadMoreInspirations().catch(() => undefined);
        }
      },
      { rootMargin: '480px 0px', threshold: 0.01 },
    );
    observer.observe(target);
    return () => observer.disconnect();
  }, [feedLoading, hasMoreInspirations, loadMoreInspirations, loadingMoreFeed]);

  useEffect(() => {
    const initialScrollY = feedState.scrollY;
    window.requestAnimationFrame(() => {
      if (initialScrollY > 0) {
        window.scrollTo({ top: initialScrollY, behavior: 'auto' });
      }
    });
    return () => {
      patchState({ scrollY: window.scrollY });
    };
  }, [patchState]);

  async function handleExecute() {
    const prompt = promptValue.trim();
    if (!prompt || loading) return;
    setLoading(true);
    setError('');
    const isEditMode = selectedFiles.length > 0;
    setMessage(isEditMode ? t('home_message_edit_sent') : t('home_message_generate_sent'));
    const imageOptions = {
      size: providerImageSize(imageScale, aspectRatio),
      aspect_ratio: aspectRatio,
      quality: imageQuality,
    };
    try {
      const submittedTask = isEditMode
        ? await editImage({ prompt, ...imageOptions }, selectedFiles)
        : await generateImage({ prompt, ...imageOptions });
      addTask(submittedTask);
      openDrawer();
      setMessage(submittedTask.status === 'running' ? t('home_message_processing') : t('home_message_queued'));
      setSelectedFiles([]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setMessage('');
    } finally {
      setLoading(false);
    }
  }

  async function handleOptimizePrompt() {
    const prompt = promptValue.trim();
    if (!prompt || optimizingPrompt) {
      if (!prompt) setError(t('home_prompt_optimizer_empty'));
      return;
    }
    setOptimizingPrompt(true);
    setError('');
    setMessage(t('home_optimizing_prompt'));
    try {
      const result = await optimizePrompt({
        prompt,
        instruction: promptInstruction.trim() || undefined,
        size: providerImageSize(imageScale, aspectRatio),
        aspect_ratio: aspectRatio,
        quality: imageQuality,
      });
      setPromptValue(result.prompt);
      setMessage(t('home_prompt_optimized'));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setMessage('');
    } finally {
      setOptimizingPrompt(false);
    }
  }

  async function handleToggleFavorite(item: FeedItem) {
    if (!item.inspirationId) return;
    if (!viewer?.authenticated) {
      setError(t('home_favorite_login_required'));
      return;
    }
    setError('');
    setFavoritePendingIds((current) => (current.includes(item.inspirationId!) ? current : [...current, item.inspirationId!]));
    try {
      const result = item.favorited
        ? await unfavoriteInspiration(item.inspirationId)
        : await favoriteInspiration(item.inspirationId);
      setFeedState((current) => ({
        ...current,
        inspirations: current.inspirations.map((inspiration) => (inspiration.id === result.item.id ? result.item : inspiration)),
      }));
      setMessage(result.item.favorited ? t('home_favorite_saved') : t('home_favorite_removed'));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setFavoritePendingIds((current) => current.filter((id) => id !== item.inspirationId));
    }
  }

  async function handleClonePrompt(prompt: string) {
    setPromptValue(prompt);
    await copyTextToClipboard(prompt);
    setMessage(t('home_prompt_copied'));
  }

  async function handleCopyPrompt() {
    if (!promptValue.trim()) {
      return;
    }
    await copyTextToClipboard(promptValue);
    setMessage(t('home_prompt_copied'));
  }

  const mergedHistory = mergeHistory([
    ...taskHistoryItems.filter((item) => item.status === 'succeeded' && Boolean(item.image_url)),
    ...history,
  ]);

  const generatedFeed: FeedItem[] = mergedHistory.map((item) => ({
    key: `history-${item.id}`,
    id: `ID:${item.id.slice(0, 4).toUpperCase()}`,
    img: item.image_url || '',
    prompt: item.prompt,
    title: item.mode.toUpperCase(),
    inspirationId: null,
    favorited: false,
  }));
  const inspirationFeed: FeedItem[] = inspirations.map((item) => ({
    key: `case-${item.id}`,
    id: item.author || item.section,
    img: item.image_url || '',
    prompt: item.prompt,
    title: item.title,
    inspirationId: item.id,
    favorited: item.favorited,
  }));
  const hasSearchInput = inspirationSearchInput.trim().length > 0;
  const visibleFeed = [...(hasSearchInput ? [] : generatedFeed), ...inspirationFeed].filter((item) => item.img);
  const scrollToTop = () => window.scrollTo({ top: 0, behavior: 'smooth' });
  const handleAspectRatioChange = (nextRatio: string) => {
    setAspectRatio(nextRatio);
    if (nextRatio === '1:1' && imageScale === '4K') {
      setImageScale('2K');
    }
  };
  const addReferenceFiles = useCallback(
    (files: File[]) => {
      const imageFiles = files.filter((file) => ['image/png', 'image/jpeg', 'image/webp'].includes(file.type));
      if (imageFiles.length === 0) {
        setError(t('home_ref_image_invalid'));
        return;
      }
      setSelectedFiles((current) => [...current, ...imageFiles]);
      setMessage(t('home_mode_edit'));
    },
    [t],
  );
  const handleReferenceImages = (event: ChangeEvent<HTMLInputElement>) => {
    addReferenceFiles(Array.from(event.target.files || []));
    event.target.value = '';
  };
  const handleReferenceDragOver = (event: DragEvent<HTMLDivElement>) => {
    if (!Array.from(event.dataTransfer.types).includes('Files')) {
      return;
    }
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    setDraggingReference(true);
  };
  const handleReferenceDragLeave = (event: DragEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setDraggingReference(false);
    }
  };
  const handleReferenceDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDraggingReference(false);
    addReferenceFiles(Array.from(event.dataTransfer.files || []));
  };
  const removeReferenceImage = (index: number) => {
    setSelectedFiles((current) => current.filter((_, currentIndex) => currentIndex !== index));
  };

  return (
    <div className="pt-24 pb-[19rem] px-4 sm:pb-56 sm:px-6 max-w-[1440px] mx-auto min-h-screen bg-[radial-gradient(ellipse_at_top,var(--color-surface-container-high),var(--color-background))] font-mono">
      <div className="flex justify-between items-end mb-8">
        <div className="flex flex-col gap-2">
           <div className="flex items-center gap-2 text-[10px] text-secondary uppercase font-bold tracking-widest">
              <span className="w-4 h-[1px] bg-secondary"></span> {t('home_scan')}
           </div>
          <h1 className="text-4xl md:text-5xl text-on-surface font-bold tracking-tighter">{t('home_title')}</h1>
          <div className="text-xs text-white/40 uppercase tracking-widest">
            {viewer?.authenticated
              ? t('home_owner', { value: viewer.user?.username || viewer.user?.email || '--' })
              : t('home_guest', { value: viewer?.guest_id?.slice(0, 8) || '--' })}
          </div>
        </div>
        <div className="hidden sm:flex gap-2">
          <button className="w-10 h-10 border border-outline-variant flex items-center justify-center text-outline-variant hover:text-primary hover:border-primary bg-surface-container-low transition-colors">
            <Grid size={20} />
          </button>
          <button className="w-10 h-10 border border-outline-variant flex items-center justify-center text-outline-variant hover:text-primary hover:border-primary bg-surface-container-low transition-colors">
            <List size={20} />
          </button>
        </div>
      </div>

      {error && <div className="mb-6 border border-error/40 bg-error/10 p-4 text-error text-xs">{error}</div>}

      <div className="mb-6 flex flex-col gap-3 border border-primary/20 bg-black/50 p-3 md:flex-row md:items-center md:justify-between">
        <label className="flex min-w-0 flex-1 items-center gap-3 border border-white/10 bg-surface-container-low/70 px-3 py-2 text-white/70 focus-within:border-primary">
          <Search className="shrink-0 text-primary/70" size={16} />
          <input
            className="min-w-0 flex-1 bg-transparent text-sm text-white outline-none placeholder:text-white/30"
            value={inspirationSearchInput}
            onChange={(event) => patchState({ inspirationSearchInput: event.target.value })}
            placeholder={t('home_case_search')}
          />
          {inspirationSearchInput ? (
            <button
              className="flex h-7 w-7 shrink-0 items-center justify-center text-white/45 transition-colors hover:text-primary"
              type="button"
              title={t('home_case_clear_search')}
              aria-label={t('home_case_clear_search')}
              onClick={() => {
                patchState({ inspirationSearchInput: '', inspirationQuery: '' });
              }}
            >
              <X size={15} />
            </button>
          ) : null}
        </label>
        <div className="shrink-0 font-code-data text-[10px] uppercase tracking-[0.24em] text-white/40">
          {inspirationQuery
            ? t('home_case_search_results', { total: inspirationTotal })
            : t('home_case_total', { total: inspirationTotal })}
        </div>
      </div>

      {feedLoading ? (
        <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 8 }).map((_, index) => (
            <div key={index} className="relative aspect-[3/4] overflow-hidden border border-primary/20 bg-black/60">
              <div className="absolute inset-0 animate-pulse bg-[linear-gradient(180deg,rgba(0,243,255,0.08),rgba(255,0,255,0.08))]" />
              <div className="absolute inset-0 bg-[radial-gradient(circle_at_top,rgba(255,255,255,0.08),transparent_55%)]" />
              <div className="absolute bottom-0 left-0 right-0 p-4">
                <div className="mb-3 h-3 w-24 animate-pulse bg-white/10" />
                <div className="mb-2 h-4 w-full animate-pulse bg-white/10" />
                <div className="h-4 w-3/4 animate-pulse bg-white/10" />
              </div>
            </div>
          ))}
          <div className="col-span-full flex items-center justify-center gap-3 py-4 text-xs uppercase tracking-[0.3em] text-primary/70">
            <Loader2 className="animate-spin" size={16} />
            {t('home_loading_feed')}
          </div>
        </div>
      ) : visibleFeed.length > 0 ? (
        <>
          <MasonryGrid
            items={visibleFeed}
            getKey={(item) => item.key}
            renderItem={(item) => {
              const canFavorite = Boolean(viewer?.authenticated && item.inspirationId);
              const favoritePending = item.inspirationId ? favoritePendingIds.includes(item.inspirationId) : false;
              return (
                <div className="overflow-hidden border border-primary/30 bg-black">
                  <button
                    className="block w-full cursor-zoom-in bg-black text-left"
                    type="button"
                    onClick={() => setPreviewItem({ imageUrl: item.img, prompt: item.prompt })}
                  >
                    <img
                      alt={item.id}
                      className="block h-auto w-full opacity-95 transition-opacity duration-300 hover:opacity-100"
                      loading="lazy"
                      src={item.img}
                    />
                  </button>
                  <div className="border-t border-primary/15 bg-surface-container-low/80 p-4">
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <div className="min-w-0 truncate text-[10px] uppercase tracking-widest text-secondary">{item.title}</div>
                      <div className="shrink-0 font-code-data text-[10px] text-white/25">{item.id}</div>
                    </div>
                    <p className="mb-3 line-clamp-3 text-sm text-white/80">{item.prompt}</p>
                    <div className={`grid gap-2 ${canFavorite ? 'grid-cols-[44px_44px_1fr]' : 'grid-cols-[44px_1fr]'}`}>
                      <button
                        className="flex h-10 items-center justify-center border border-white/10 bg-white/5 text-white/70 transition-all duration-300 hover:border-primary hover:text-primary"
                        type="button"
                        onClick={() => setPreviewItem({ imageUrl: item.img, prompt: item.prompt })}
                        title={t('history_preview')}
                      >
                        <Maximize2 size={15} />
                      </button>
                      {canFavorite ? (
                        <button
                          className={`flex h-10 items-center justify-center border transition-all duration-300 disabled:cursor-not-allowed disabled:opacity-50 ${
                            item.favorited
                              ? 'border-secondary/50 bg-secondary/15 text-secondary hover:bg-secondary/25'
                              : 'border-white/10 bg-white/5 text-white/70 hover:border-secondary hover:text-secondary'
                          }`}
                          type="button"
                          disabled={favoritePending}
                          onClick={() => handleToggleFavorite(item)}
                          title={item.favorited ? t('home_unfavorite_case') : t('home_favorite_case')}
                        >
                          {favoritePending ? (
                            <Loader2 className="animate-spin" size={15} />
                          ) : (
                            <Heart className={item.favorited ? 'fill-secondary' : ''} size={15} />
                          )}
                        </button>
                      ) : null}
                      <button
                        onClick={(event) => {
                          event.stopPropagation();
                          handleClonePrompt(item.prompt).catch(() => undefined);
                        }}
                        className="flex h-10 items-center justify-center gap-2 bg-primary px-3 text-xs font-black uppercase text-black shadow-[0_0_10px_rgba(0,243,255,0.35)] transition-all duration-300 hover:bg-white hover:shadow-white/40"
                      >
                        <RefreshCw size={14} />
                        {t('home_clone_prompt')}
                      </button>
                    </div>
                  </div>
                </div>
              );
            }}
          />
          <div className="flex flex-col items-center gap-4 py-8">
            {loadingMoreFeed ? (
              <div className="flex items-center justify-center gap-3 text-xs uppercase tracking-[0.3em] text-primary/70">
                <Loader2 className="animate-spin" size={16} />
                {t('home_loading_more')}
              </div>
            ) : hasMoreInspirations ? (
              <button
                className="border border-primary/30 bg-primary/5 px-6 py-3 text-xs font-bold uppercase tracking-[0.25em] text-primary transition-colors hover:bg-primary/10"
                type="button"
                onClick={() => loadMoreInspirations().catch(() => undefined)}
              >
                {t('home_load_more')}
              </button>
            ) : (
              <div className="text-xs uppercase tracking-[0.3em] text-white/35">{t('home_all_loaded')}</div>
            )}
            <div ref={loadMoreRef} className="h-2 w-full" />
          </div>
        </>
      ) : (
        <div className="flex min-h-[320px] items-center justify-center border border-primary/20 bg-black/50 px-6 text-sm text-white/50">
          {t('home_empty_feed')}
        </div>
      )}

      <div
        className={`fixed bottom-3 left-3 right-3 z-50 mx-auto max-w-[1080px] rounded-sm border bg-surface-container/90 p-3 font-mono shadow-[0_-20px_40px_rgba(0,0,0,0.75)] backdrop-blur-xl transition-colors md:bottom-5 md:p-4 ${
          draggingReference ? 'border-secondary bg-secondary/10' : 'border-primary/40'
        }`}
        onDragEnter={handleReferenceDragOver}
        onDragLeave={handleReferenceDragLeave}
        onDragOver={handleReferenceDragOver}
        onDrop={handleReferenceDrop}
      >
        <div className="mb-2 flex min-w-0 items-center gap-3">
          <div className="flex shrink-0 items-center gap-2 border-r border-white/10 pr-3 text-[10px] text-white/50">
            <span className="h-2 w-2 rounded-full bg-secondary" />
            {t('home_mode')}: {selectedFiles.length ? t('home_mode_edit') : t('home_mode_generate')}
          </div>
          <div className="hidden items-center gap-2 text-[10px] uppercase tracking-widest text-white/45 md:flex">
            <span>{SIZE_LABELS[imageScale] || imageScale}</span>
            <span>{aspectRatio}</span>
            <span>{providerImageSize(imageScale, aspectRatio)}</span>
            <span>{imageQuality}</span>
          </div>
          <div className="min-w-0 flex-1 truncate text-[10px] uppercase tracking-widest text-primary">
            {message || (promptValue ? t('home_message_loaded') : t('home_message_waiting'))}
          </div>
        </div>

        <div className="mb-2 grid grid-cols-3 items-end gap-2 lg:grid-cols-[128px_112px_104px_1fr_auto]">
          <GenerationSelect
            label={t('home_size')}
            value={imageScale}
            onChange={setImageScale}
            options={SIZE_OPTIONS}
            getOptionLabel={(option) => SIZE_LABELS[option] || option}
            isOptionDisabled={(option) => !isSupportedImagePreset(option, aspectRatio)}
          />
          <GenerationSelect label={t('home_aspect_ratio')} value={aspectRatio} onChange={handleAspectRatioChange} options={ASPECT_RATIO_OPTIONS} />
          <GenerationSelect label={t('home_quality')} value={imageQuality} onChange={setImageQuality} options={QUALITY_OPTIONS} />
          <div className="col-span-3 flex min-w-0 gap-2 lg:col-span-2">
            <label className="flex h-9 min-w-0 flex-1 items-center gap-2 border border-primary/20 bg-black px-3 text-primary focus-within:border-primary">
              <Sparkles className="shrink-0 text-secondary/80" size={14} />
              <input
                className="min-w-0 flex-1 bg-transparent text-xs text-primary outline-none placeholder:text-primary/25"
                value={promptInstruction}
                onChange={(event) => setPromptInstruction(event.target.value)}
                placeholder={t('home_prompt_instruction')}
              />
            </label>
            <button
              className="flex h-9 shrink-0 items-center justify-center gap-2 border border-secondary/50 bg-secondary/10 px-3 text-[11px] font-black uppercase tracking-widest text-secondary transition-colors hover:bg-secondary hover:text-black disabled:cursor-not-allowed disabled:opacity-40"
              type="button"
              disabled={optimizingPrompt || !promptValue.trim()}
              onClick={handleOptimizePrompt}
            >
              {optimizingPrompt ? <Loader2 className="animate-spin" size={13} /> : <Sparkles size={13} />}
              <span className="hidden sm:inline">{optimizingPrompt ? t('home_optimizing_prompt') : t('home_optimize_prompt')}</span>
              <span className="sm:hidden">AI</span>
            </button>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_auto]">
          <div className="min-w-0">
            <textarea
              value={promptValue}
              onChange={(e) => setPromptValue(e.target.value)}
              className="h-16 w-full resize-none border border-primary/20 bg-black p-2.5 text-sm text-primary shadow-inner focus:border-primary focus:outline-none placeholder:text-primary/20 md:h-20 md:p-3"
              placeholder={t('home_placeholder')}
            ></textarea>
            <div className="mt-1 flex items-center justify-between gap-3 text-[8px] uppercase leading-none text-primary/40">
              <button
                className="flex items-center gap-1 text-primary/60 transition-colors hover:text-primary"
                type="button"
                onClick={() => setPromptEditorOpen(true)}
                title={t('prompt_editor_expand')}
              >
                <Maximize2 size={10} />
                {t('prompt_editor_expand')}
              </button>
              <span>UTF-8 // AI-GEN // [{promptValue.length}/8000]</span>
            </div>
          </div>

          <div className="flex min-w-0 gap-2 sm:shrink-0">
            <input
              ref={fileInputRef}
              className="hidden"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              multiple
              onChange={handleReferenceImages}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="group relative flex h-12 w-14 shrink-0 cursor-pointer flex-col items-center justify-center border border-dashed border-primary/20 transition-colors hover:bg-primary/5 sm:h-16 md:h-20 md:w-16"
              title={t('home_ref_image')}
            >
              <ImagePlus className="mb-1 h-5 w-5 text-white/30 transition-colors group-hover:text-primary" />
              <span className="max-w-full truncate px-1 text-[8px] uppercase text-white/40 group-hover:text-primary">{t('home_ref_image')}</span>
            </button>
            <button
              onClick={handleExecute}
              disabled={loading || !promptValue.trim()}
              className="flex h-12 min-w-0 flex-1 flex-col items-center justify-center bg-primary text-black font-black shadow-[0_0_15px_rgba(0,243,255,0.4)] transition-transform hover:scale-95 disabled:opacity-40 disabled:hover:scale-100 sm:h-16 sm:w-20 sm:flex-none md:h-20 md:w-28"
            >
              {loading ? <Loader2 className="animate-spin" size={22} /> : <span className="mb-[-4px] text-lg md:text-xl">{t('home_execute')}</span>}
              <span className="text-[9px] italic opacity-70 md:text-[10px]">{selectedFiles.length ? t('home_edit') : t('home_generate')}</span>
            </button>
          </div>
        </div>

        {selectedPreviews.length > 0 && (
          <div className="mt-2 flex max-w-full gap-2 overflow-x-auto pb-1">
            {selectedPreviews.map((preview, index) => (
              <div key={preview.id} className="group/reference relative h-14 w-12 shrink-0 overflow-hidden border border-primary/20 bg-black">
                <button
                  type="button"
                  className="h-full w-full cursor-zoom-in bg-black"
                  title={preview.name}
                  onClick={() => setPreviewItem({ imageUrl: preview.url, prompt: preview.name })}
                >
                  <img alt={preview.name} className="h-full w-full object-cover" src={preview.url} />
                </button>
                <button
                  type="button"
                  aria-label={t('modal_close')}
                  className="absolute right-1 top-1 flex h-5 w-5 items-center justify-center border border-white/15 bg-black/70 text-white/80 opacity-100 transition-colors hover:border-error hover:text-error sm:opacity-0 sm:group-hover/reference:opacity-100"
                  onClick={() => removeReferenceImage(index)}
                >
                  <X size={12} />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <ImagePreviewModal
        imageUrl={previewItem?.imageUrl || null}
        alt={previewItem?.prompt || 'preview'}
        subtitle={previewItem?.prompt}
        onClose={() => setPreviewItem(null)}
      />
      <PromptEditorModal
        open={promptEditorOpen}
        value={promptValue}
        onChange={setPromptValue}
        onClose={() => setPromptEditorOpen(false)}
        onCopy={() => handleCopyPrompt().catch(() => undefined)}
      />
      {showBackToTop ? (
        <button
          className="fixed bottom-[14rem] right-5 z-40 flex h-11 w-11 items-center justify-center border border-primary/40 bg-black/80 text-primary shadow-[0_0_18px_rgba(0,243,255,0.22)] backdrop-blur transition-colors hover:bg-primary hover:text-black md:bottom-28 md:right-8"
          type="button"
          title={t('home_back_to_top')}
          aria-label={t('home_back_to_top')}
          onClick={scrollToTop}
        >
          <ArrowUp size={18} />
        </button>
      ) : null}
    </div>
  );
}

function GenerationSelect({
  label,
  value,
  options,
  onChange,
  isOptionDisabled,
  getOptionLabel,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  isOptionDisabled?: (value: string) => boolean;
  getOptionLabel?: (value: string) => string;
}) {
  return (
    <label className="min-w-0">
      <span className="mb-0.5 block truncate text-[8px] uppercase tracking-[0.18em] text-white/40">{label}</span>
      <select
        className="h-9 w-full border border-primary/20 bg-black px-2 text-xs uppercase text-primary outline-none transition-colors focus:border-primary"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option} value={option} disabled={isOptionDisabled?.(option) || false}>
            {getOptionLabel?.(option) || option}
          </option>
        ))}
      </select>
    </label>
  );
}

function normalizeImageScale(value: string | undefined) {
  const normalized = (value || '').trim().toUpperCase();
  if (SIZE_OPTIONS.includes(normalized)) {
    return normalized;
  }
  if (SIZE_BY_PRESET_VALUE[normalized]) {
    return SIZE_BY_PRESET_VALUE[normalized];
  }
  if (/^1\d{3}x1\d{3}$/i.test(normalized)) {
    return '1K';
  }
  if (/^2\d{3}x|x2\d{3}$/i.test(normalized)) {
    return '2K';
  }
  if (/^[34]\d{3}x|x[34]\d{3}$/i.test(normalized)) {
    return '4K';
  }
  return '2K';
}

function providerImageSize(scale: string, ratio: string) {
  return SIZE_PRESETS[scale]?.[ratio] || SIZE_PRESETS['2K']['1:1'];
}

function isSupportedImagePreset(scale: string, ratio: string) {
  return Boolean(SIZE_PRESETS[scale]?.[ratio]);
}
