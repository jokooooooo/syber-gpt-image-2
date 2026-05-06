import { useCallback, useEffect, useRef, useState } from 'react';
import type { ChangeEvent, DragEvent } from 'react';
import { Link } from 'react-router-dom';
import { ArrowUp, Heart, ImagePlus, Maximize2, Minimize2, RefreshCw, Loader2, Search, Sparkles, X } from 'lucide-react';
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
  searchInspirationsWithAI,
  unfavoriteInspiration,
} from '../api';
import { useAuth } from '../auth';
import { copyTextToClipboard } from '../clipboard';
import ImagePreviewModal from '../components/ImagePreviewModal';
import MasonryGrid from '../components/MasonryGrid';
import ModelBadge from '../components/ModelBadge';
import PromptEditorModal from '../components/PromptEditorModal';
import { useHomeFeed } from '../homeFeed';
import { groupHistoryItems, mergeHistoryItems } from '../historyGroups';
import { createReferenceEntry, REFERENCE_ROLE_OPTIONS, ReferenceImageEntry } from '../referenceImages';
import {
  ASPECT_RATIO_OPTIONS,
  IMAGE_COUNT_OPTIONS,
  isSupportedImagePreset,
  normalizeImageScale,
  providerImageSize,
  QUALITY_OPTIONS,
  SIZE_LABELS,
  SIZE_OPTIONS,
} from '../imageOptions';
import { useSite } from '../site';
import { useTasks } from '../tasks';
import GenerationSelect from '../components/GenerationSelect';

const FEED_PAGE_SIZE = 24;
const PROMPT_TRANSFER_KEY = 'joko_pending_prompt';

function groupHistoryForFeed(items: HistoryItem[]): FeedItem[] {
  return groupHistoryItems(items)
    .filter((group) => group.images.length > 0)
    .map((group) => ({
      key: `history-${group.key}`,
      id: `ID:${group.first.id.slice(0, 4).toUpperCase()}`,
      img: group.images[0].url,
      images: group.images.map((image) => ({ id: image.id, url: image.url, prompt: image.prompt })),
      prompt: group.taskPrompt,
      title: group.title,
      inspirationId: null,
      favorited: false,
    }));
}

type FeedItem = {
  key: string;
  id: string;
  img: string;
  images: { id: string; url: string; prompt: string }[];
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
  const [selectedReferences, setSelectedReferences] = useState<ReferenceImageEntry[]>([]);
  const [selectedPreviews, setSelectedPreviews] = useState<{ id: string; name: string; url: string }[]>([]);
  const [imageScale, setImageScale] = useState('2K');
  const [aspectRatio, setAspectRatio] = useState('1:1');
  const [imageQuality, setImageQuality] = useState('auto');
  const [imageCount, setImageCount] = useState('1');
  const [previewItem, setPreviewItem] = useState<{ imageUrl: string; prompt: string } | null>(null);
  const [promptEditorOpen, setPromptEditorOpen] = useState(false);
  const [generationPanelExpanded, setGenerationPanelExpanded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [optimizingPrompt, setOptimizingPrompt] = useState(false);
  const [aiSearching, setAiSearching] = useState(false);
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
    inspirationSearchMode,
    inspirationAIQuery,
  } = feedState;

  useEffect(() => {
    const pendingPrompt = window.sessionStorage.getItem(PROMPT_TRANSFER_KEY);
    if (pendingPrompt) {
      setPromptValue(pendingPrompt);
      setGenerationPanelExpanded(true);
      window.sessionStorage.removeItem(PROMPT_TRANSFER_KEY);
    }
  }, []);

  useEffect(() => {
    const previews = selectedReferences.map((reference) => ({
      id: reference.id,
      name: reference.file.name,
      url: URL.createObjectURL(reference.file),
    }));
    setSelectedPreviews(previews);
    return () => {
      previews.forEach((preview) => URL.revokeObjectURL(preview.url));
    };
  }, [selectedReferences]);

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
    if (inspirationSearchMode === 'ai') {
      return;
    }
    const handle = window.setTimeout(() => {
      patchState({ inspirationQuery: inspirationSearchInput.trim() });
    }, 350);
    return () => window.clearTimeout(handle);
  }, [inspirationSearchInput, inspirationSearchMode, patchState]);

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
    if (
      feedState.initialized &&
      feedState.loadedOwnerId === ownerId &&
      feedState.loadedQuery === (query || '') &&
      feedState.loadedSearchMode === inspirationSearchMode
    ) {
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
    const inspirationTask = inspirationSearchMode === 'ai' && query
      ? searchInspirationsWithAI({ limit: FEED_PAGE_SIZE, offset: 0, query })
      : getInspirations({ limit: FEED_PAGE_SIZE, offset: 0, q: query });
    const task = Promise.all([getHistory({ limit: 12 }), inspirationTask]);
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
          inspirationAIQuery: 'query' in inspirationData && inspirationSearchMode === 'ai' ? inspirationData.query : '',
          loadedSearchMode: inspirationSearchMode,
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
          if (inspirationSearchMode === 'ai') {
            setAiSearching(false);
          }
        }
      });
    return () => {
      cancelled = true;
    };
  }, [
    feedState.initialized,
    feedState.loadedOwnerId,
    feedState.loadedQuery,
    feedState.loadedSearchMode,
    inspirationQuery,
    inspirationSearchMode,
    patchState,
    viewer?.owner_id,
  ]);

  const loadMoreInspirations = useCallback(async () => {
    if (feedLoading || loadingMoreFeed || !hasMoreInspirations) {
      return;
    }
    patchState({ loadingMoreFeed: true });
    try {
      const query = inspirationSearchMode === 'ai' ? inspirationAIQuery || inspirationQuery || undefined : inspirationQuery || undefined;
      const data = await getInspirations({
        limit: FEED_PAGE_SIZE,
        offset: inspirationOffset,
        q: query,
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
  }, [
    feedLoading,
    hasMoreInspirations,
    inspirationAIQuery,
    inspirationOffset,
    inspirationQuery,
    inspirationSearchMode,
    inspirationTotal,
    loadingMoreFeed,
    patchState,
    setFeedState,
  ]);

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
    if (!prompt) return;
    if (loading) return;
    if (!viewer?.authenticated) {
      setGenerationPanelExpanded(true);
      setError(t('home_generation_login_required'));
      return;
    }
    setLoading(true);
    setError('');
    const requestedImageCount = Math.max(1, Math.min(9, Number(imageCount) || 1));
    const imageOptions = {
      size: providerImageSize(imageScale, aspectRatio),
      aspect_ratio: aspectRatio,
      quality: imageQuality,
    };
    try {
      const isEditMode = selectedReferences.length > 0;
      setMessage(isEditMode ? t('home_message_edit_sent') : t('home_message_generate_sent'));
      const submittedTask = isEditMode
        ? await editImage(
            { prompt, ...imageOptions, n: requestedImageCount },
            selectedReferences.map((reference) => ({
              file: reference.file,
              role: reference.role,
              note: reference.note,
            })),
          )
        : await generateImage({ prompt, ...imageOptions, n: requestedImageCount });
      setSelectedReferences([]);
      addTask(submittedTask);
      openDrawer();
      setMessage(submittedTask.status === 'running' ? t('home_message_processing') : t('home_message_queued'));
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

  async function handleAISearch() {
    const query = inspirationSearchInput.trim();
    if (!query || aiSearching) {
      return;
    }
    setAiSearching(true);
    setError('');
    patchState({
      inspirationSearchMode: 'ai',
      inspirationQuery: query,
      inspirationAIQuery: '',
      feedLoading: true,
      initialized: false,
    });
  }

  function setKeywordSearchMode() {
    patchState({
      inspirationSearchMode: 'keyword',
      inspirationAIQuery: '',
      inspirationQuery: inspirationSearchInput.trim(),
      initialized: false,
    });
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
    setGenerationPanelExpanded(true);
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

  const mergedHistory = mergeHistoryItems([
    ...taskHistoryItems.filter((item) => item.status === 'succeeded' && Boolean(item.image_url)),
    ...history,
  ]);

  const generatedFeed: FeedItem[] = groupHistoryForFeed(mergedHistory);
  const inspirationFeed: FeedItem[] = inspirations.map((item) => ({
    key: `case-${item.id}`,
    id: item.author || item.section,
    img: item.image_url || '',
    images: item.image_url ? [{ id: item.id, url: item.image_url, prompt: item.prompt }] : [],
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
  const getDroppedImages = (files: File[]) => files.filter((file) => ['image/png', 'image/jpeg', 'image/webp'].includes(file.type));
  const addReferenceFiles = useCallback(
    (files: File[]) => {
      const imageFiles = getDroppedImages(files);
      if (imageFiles.length === 0) {
        setError(t('home_ref_image_invalid'));
        return;
      }
      setSelectedReferences((current) => [...current, ...imageFiles.map((file) => createReferenceEntry(file))].slice(0, 8));
      setGenerationPanelExpanded(true);
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
    const files = Array.from(event.dataTransfer.files || []);
    addReferenceFiles(files);
  };
  const removeReferenceImage = (index: number) => {
    setSelectedReferences((current) => current.filter((_, currentIndex) => currentIndex !== index));
  };
  const updateReferenceImage = (index: number, patch: Partial<Pick<ReferenceImageEntry, 'role' | 'note'>>) => {
    setSelectedReferences((current) =>
      current.map((reference, currentIndex) => (currentIndex === index ? { ...reference, ...patch } : reference)),
    );
  };

  return (
    <div className={`pt-24 px-4 sm:px-6 max-w-[1440px] mx-auto min-h-screen bg-[radial-gradient(ellipse_at_top,var(--color-surface-container-high),var(--color-background))] font-mono ${generationPanelExpanded ? 'pb-[19rem] sm:pb-56' : 'pb-28 sm:pb-32'}`}>
      <div className="flex justify-between items-end mb-8">
        <div className="flex flex-col gap-2">
           <div className="flex items-center gap-2 text-[10px] text-secondary uppercase font-bold tracking-widest">
              <span className="w-4 h-[1px] bg-secondary"></span> {t('home_scan')}
           </div>
          <h1 className="text-4xl md:text-5xl text-on-surface font-bold tracking-tighter">{t('home_title')}</h1>
          <div className="flex flex-wrap items-center gap-3">
            <ModelBadge />
            <div className="text-xs uppercase tracking-widest text-white/40">
              {viewer?.authenticated
                ? t('home_owner', { value: viewer.user?.username || viewer.user?.email || '--' })
                : t('home_guest', { value: viewer?.guest_id?.slice(0, 8) || '--' })}
            </div>
          </div>
        </div>
        <div className="hidden min-w-[260px] grid-cols-2 gap-2 sm:grid">
          <Link className="flex h-10 items-center justify-center border border-primary bg-primary/15 text-xs font-bold uppercase tracking-widest text-primary" to="/">
            {t('home_tab_general')}
          </Link>
          <Link className="flex h-10 items-center justify-center border border-white/10 bg-black/30 text-xs font-bold uppercase tracking-widest text-white/50 transition-colors hover:border-secondary hover:text-secondary" to="/ecommerce">
            {t('home_tab_ecommerce')}
          </Link>
        </div>
      </div>

      {error && <div className="mb-6 border border-error/40 bg-error/10 p-4 text-error text-xs">{error}</div>}

      <div className="mb-6 flex flex-col gap-3 border border-primary/20 bg-black/50 p-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row">
          <div className="grid h-10 shrink-0 grid-cols-2 border border-white/10 bg-surface-container-low/70 p-1">
            <button
              className={`px-3 text-[10px] font-bold uppercase tracking-widest transition-colors ${
                inspirationSearchMode === 'keyword' ? 'bg-primary text-black' : 'text-white/55 hover:text-primary'
              }`}
              type="button"
              onClick={setKeywordSearchMode}
            >
              {t('home_case_search_keyword')}
            </button>
            <button
              className={`px-3 text-[10px] font-bold uppercase tracking-widest transition-colors ${
                inspirationSearchMode === 'ai' ? 'bg-secondary text-black' : 'text-white/55 hover:text-secondary'
              }`}
              type="button"
              onClick={handleAISearch}
              disabled={aiSearching || !inspirationSearchInput.trim()}
            >
              {aiSearching ? <Loader2 className="mx-auto animate-spin" size={14} /> : t('home_case_search_ai')}
            </button>
          </div>
          <label className="flex min-w-0 flex-1 items-center gap-3 border border-white/10 bg-surface-container-low/70 px-3 py-2 text-white/70 focus-within:border-primary">
            <Search className="shrink-0 text-primary/70" size={16} />
            <input
              className="min-w-0 flex-1 bg-transparent text-sm text-white outline-none placeholder:text-white/30"
              value={inspirationSearchInput}
              onChange={(event) => {
                patchState({ inspirationSearchInput: event.target.value });
                if (inspirationSearchMode === 'ai') {
                  patchState({ inspirationAIQuery: '' });
                }
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && inspirationSearchMode === 'ai') {
                  event.preventDefault();
                  handleAISearch().catch(() => undefined);
                }
              }}
              placeholder={t('home_case_search')}
            />
            {inspirationSearchInput ? (
              <button
                className="flex h-7 w-7 shrink-0 items-center justify-center text-white/45 transition-colors hover:text-primary"
                type="button"
                title={t('home_case_clear_search')}
                aria-label={t('home_case_clear_search')}
                onClick={() => {
                  patchState({
                    inspirationSearchInput: '',
                    inspirationQuery: '',
                    inspirationAIQuery: '',
                    inspirationSearchMode: 'keyword',
                    initialized: false,
                  });
                }}
              >
                <X size={15} />
              </button>
            ) : null}
          </label>
        </div>
        <div className="shrink-0 font-code-data text-[10px] uppercase tracking-[0.24em] text-white/40">
          {aiSearching
            ? t('home_case_ai_searching')
            : inspirationSearchMode === 'ai' && inspirationAIQuery
              ? t('home_case_ai_query', { value: inspirationAIQuery })
              : inspirationQuery
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
              const images = item.images.length > 0 ? item.images : [{ id: item.id, url: item.img, prompt: item.prompt }];
              const isBatch = images.length > 1;
              return (
                <div className="overflow-hidden border border-primary/30 bg-black">
                  {isBatch ? (
                    <div className="grid grid-cols-3 gap-1 bg-black p-1">
                      {images.map((image, index) => (
                        <button
                          key={image.id}
                          className="relative aspect-square cursor-zoom-in overflow-hidden bg-black text-left"
                          type="button"
                          onClick={() => setPreviewItem({ imageUrl: image.url, prompt: image.prompt })}
                        >
                          <img
                            alt={`${item.id}-${index + 1}`}
                            className="h-full w-full object-cover opacity-95 transition-opacity duration-300 hover:opacity-100"
                            loading="lazy"
                            src={image.url}
                          />
                        </button>
                      ))}
                    </div>
                  ) : (
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
                  )}
                  <div className="border-t border-primary/15 bg-surface-container-low/80 p-4">
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <div className="min-w-0 truncate text-[10px] uppercase tracking-widest text-secondary">{item.title}</div>
                      <div className="shrink-0 font-code-data text-[10px] text-white/25">
                        {item.id}
                        {isBatch ? ` x${images.length}` : ''}
                      </div>
                    </div>
                    <p className="mb-3 line-clamp-3 text-sm text-white/80">{item.prompt}</p>
                    <div className={`grid gap-2 ${canFavorite ? 'grid-cols-[44px_44px_1fr]' : 'grid-cols-[44px_1fr]'}`}>
                      <button
                        className="flex h-10 items-center justify-center border border-white/10 bg-white/5 text-white/70 transition-all duration-300 hover:border-primary hover:text-primary"
                        type="button"
                        onClick={() => setPreviewItem({ imageUrl: images[0]?.url || item.img, prompt: item.prompt })}
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
        className={`fixed bottom-3 left-3 right-3 z-50 mx-auto rounded-sm border bg-surface-container/90 font-mono shadow-[0_-20px_40px_rgba(0,0,0,0.75)] backdrop-blur-xl transition-colors md:bottom-5 ${
          generationPanelExpanded ? 'max-w-[1080px] p-3 md:p-4' : 'max-w-[920px] p-2 md:p-2.5'
        } ${
          draggingReference ? 'border-secondary bg-secondary/10' : 'border-primary/40'
        }`}
        onDragEnter={handleReferenceDragOver}
        onDragLeave={handleReferenceDragLeave}
        onDragOver={handleReferenceDragOver}
        onDrop={handleReferenceDrop}
      >
        <input
          ref={fileInputRef}
          className="hidden"
          type="file"
          accept="image/png,image/jpeg,image/webp"
          multiple
          onChange={handleReferenceImages}
        />
        <div className={`${generationPanelExpanded ? 'mb-2' : ''} flex min-w-0 items-center gap-2 md:gap-3`}>
          <div className="flex shrink-0 items-center gap-2 border-r border-white/10 pr-3 text-[10px] text-white/50">
            <span className="h-2 w-2 rounded-full bg-secondary" />
            {t('home_mode')}: {selectedReferences.length ? t('home_mode_edit') : t('home_mode_generate')}
          </div>
          <div className="hidden items-center gap-2 text-[10px] uppercase tracking-widest text-white/45 md:flex">
            <span>{SIZE_LABELS[imageScale] || imageScale}</span>
            <span>{aspectRatio}</span>
            <span>{providerImageSize(imageScale, aspectRatio)}</span>
            <span>{imageQuality}</span>
            {Number(imageCount) > 1 ? <span>x{imageCount}</span> : null}
          </div>
          <button
            type="button"
            className="min-w-0 flex-1 truncate text-left text-[10px] tracking-widest text-primary transition-colors hover:text-secondary"
            onClick={() => setGenerationPanelExpanded(true)}
            title={generationPanelExpanded ? undefined : t('home_panel_expand')}
          >
            {message || (promptValue ? promptValue : t('home_message_waiting'))}
          </button>
          {!generationPanelExpanded ? (
            <>
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                className="flex h-9 w-9 shrink-0 items-center justify-center border border-dashed border-primary/25 text-white/45 transition-colors hover:border-primary hover:text-primary"
                title={t('home_ref_image')}
              >
                <ImagePlus size={15} />
              </button>
              <button
                onClick={handleExecute}
                disabled={loading || !promptValue.trim()}
                className="flex h-9 shrink-0 items-center justify-center bg-primary px-3 text-[10px] font-black uppercase tracking-widest text-black shadow-[0_0_12px_rgba(0,243,255,0.35)] transition-transform hover:scale-95 disabled:opacity-40 disabled:hover:scale-100"
              >
                {loading ? <Loader2 className="animate-spin" size={15} /> : t('home_execute')}
              </button>
              <button
                type="button"
                className="flex h-9 w-9 shrink-0 items-center justify-center border border-secondary/40 text-secondary transition-colors hover:bg-secondary hover:text-black"
                title={t('home_panel_expand')}
                aria-label={t('home_panel_expand')}
                onClick={() => setGenerationPanelExpanded(true)}
              >
                <Maximize2 size={15} />
              </button>
            </>
          ) : (
            <button
              type="button"
              className="flex h-8 w-8 shrink-0 items-center justify-center border border-white/10 text-white/55 transition-colors hover:border-primary hover:text-primary"
              title={t('home_panel_collapse')}
              aria-label={t('home_panel_collapse')}
              onClick={() => setGenerationPanelExpanded(false)}
            >
              <Minimize2 size={14} />
            </button>
          )}
        </div>

        {generationPanelExpanded ? (
          <>
            <div className="mb-2 grid grid-cols-2 items-end gap-2 sm:grid-cols-4 lg:grid-cols-[128px_112px_104px_84px_1fr_auto]">
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
              <GenerationSelect
                label={t('home_image_count')}
                value={imageCount}
                onChange={setImageCount}
                options={IMAGE_COUNT_OPTIONS}
              />
              <div className="col-span-2 flex min-w-0 gap-2 sm:col-span-4 lg:col-span-2">
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
                  <span className="text-[9px] italic opacity-70 md:text-[10px]">{selectedReferences.length ? t('home_edit') : t('home_generate')}</span>
                </button>
              </div>
            </div>
          </>
        ) : null}

        {selectedPreviews.length > 0 && (
          <div className="mt-2 flex max-w-full gap-2 overflow-x-auto pb-1">
            {selectedPreviews.map((preview, index) => (
              <div key={preview.id} className="group/reference relative grid w-48 shrink-0 grid-cols-[56px_1fr] gap-2 border border-primary/20 bg-black p-1.5">
                <div className="relative h-20 overflow-hidden border border-white/10 bg-black">
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
                <div className="min-w-0">
                  <label className="mb-1 block">
                    <span className="mb-0.5 block text-[8px] uppercase tracking-widest text-white/35">{t('reference_role')}</span>
                    <select
                      className="h-7 w-full border border-white/10 bg-black px-1 text-[10px] text-primary outline-none focus:border-primary"
                      value={selectedReferences[index]?.role || ''}
                      onChange={(event) => updateReferenceImage(index, { role: event.target.value })}
                    >
                      {REFERENCE_ROLE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {t(option.labelKey)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="block">
                    <span className="mb-0.5 block text-[8px] uppercase tracking-widest text-white/35">{t('reference_note')}</span>
                    <input
                      className="h-7 w-full border border-white/10 bg-black px-1 text-[10px] text-white/75 outline-none placeholder:text-white/25 focus:border-primary"
                      value={selectedReferences[index]?.note || ''}
                      onChange={(event) => updateReferenceImage(index, { note: event.target.value })}
                      placeholder={t('reference_note_placeholder')}
                    />
                  </label>
                </div>
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
          className={`fixed right-5 z-40 flex h-11 w-11 items-center justify-center border border-primary/40 bg-black/80 text-primary shadow-[0_0_18px_rgba(0,243,255,0.22)] backdrop-blur transition-colors hover:bg-primary hover:text-black md:right-8 ${
            generationPanelExpanded ? 'bottom-[14rem] md:bottom-28' : 'bottom-24 md:bottom-24'
          }`}
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
