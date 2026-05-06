import { useEffect, useState } from 'react';
import { ArrowDown, HeartOff, Loader2, Maximize2, RefreshCw, Search } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { formatDate, getFavoriteInspirations, InspirationItem, unfavoriteInspiration } from '../api';
import { useAuth } from '../auth';
import { copyTextToClipboard } from '../clipboard';
import ImagePreviewModal from '../components/ImagePreviewModal';
import MasonryGrid from '../components/MasonryGrid';
import { useNotifier } from '../notifications';
import { useSite } from '../site';

const FAVORITE_PAGE_SIZE = 24;
const PROMPT_TRANSFER_KEY = 'joko_pending_prompt';

export default function Favorites() {
  const { viewer } = useAuth();
  const { t } = useSite();
  const { notifyError, notifySuccess } = useNotifier();
  const navigate = useNavigate();
  const [items, setItems] = useState<InspirationItem[]>([]);
  const [query, setQuery] = useState('');
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [removingIds, setRemovingIds] = useState<string[]>([]);
  const [previewItem, setPreviewItem] = useState<InspirationItem | null>(null);

  async function load(nextOffset = 0, append = false) {
    if (!viewer?.authenticated) {
      return;
    }
    setLoading(true);
    try {
      const data = await getFavoriteInspirations({
        limit: FAVORITE_PAGE_SIZE,
        offset: nextOffset,
        q: query.trim() || undefined,
      });
      setItems((current) => (append ? [...current, ...data.items] : data.items));
      setOffset(nextOffset + data.items.length);
      setTotal(Number(data.total ?? data.items.length));
    } catch (err) {
      notifyError(err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load(0, false).catch(() => undefined);
  }, [viewer?.owner_id]);

  async function handleUnfavorite(item: InspirationItem) {
    setRemovingIds((current) => (current.includes(item.id) ? current : [...current, item.id]));
    try {
      await unfavoriteInspiration(item.id);
      setItems((current) => current.filter((favorite) => favorite.id !== item.id));
      setTotal((current) => Math.max(0, current - 1));
      notifySuccess(t('home_favorite_removed'));
    } catch (err) {
      notifyError(err);
    } finally {
      setRemovingIds((current) => current.filter((id) => id !== item.id));
    }
  }

  async function handleClonePrompt(item: InspirationItem) {
    window.sessionStorage.setItem(PROMPT_TRANSFER_KEY, item.prompt);
    await copyTextToClipboard(item.prompt);
    navigate('/');
  }

  if (!viewer?.authenticated) {
    return (
      <div className="md:ml-64 mx-auto min-h-screen max-w-[960px] px-6 py-8 pt-24 font-mono">
        <div className="border border-primary/20 bg-black/50 p-8 text-center">
          <div className="mb-3 text-[10px] font-bold uppercase tracking-[0.25em] text-primary">{t('favorites_tag')}</div>
          <h1 className="mb-3 text-3xl font-black tracking-tight text-white">{t('favorites_title')}</h1>
          <p className="mb-6 text-sm text-white/50">{t('favorites_login_required')}</p>
          <Link
            className="inline-flex h-11 items-center justify-center border border-primary/40 px-6 text-xs font-bold uppercase tracking-widest text-primary transition-colors hover:bg-primary/10"
            to="/login"
          >
            {t('top_login')}
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="md:ml-64 mx-auto min-h-screen max-w-[1440px] bg-[radial-gradient(ellipse_at_top,var(--color-surface-container-high),var(--color-background))] px-6 py-8 pt-24 pb-12 font-mono md:px-12">
      <div className="mb-10 flex flex-col items-start justify-between gap-6 border-b border-white/10 pb-6 md:flex-row md:items-end">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-secondary">
            <span className="h-[1px] w-4 bg-secondary" /> {t('favorites_tag')}
          </div>
          <h1 className="text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">{t('favorites_title')}</h1>
          <p className="text-sm text-white/50">{t('favorites_subtitle')}</p>
        </div>

        <div className="flex w-full gap-4 md:w-auto">
          <label className="flex min-w-0 flex-1 items-center gap-3 border border-primary/20 bg-black px-3 py-2 text-primary focus-within:border-primary md:w-72">
            <Search className="shrink-0 text-primary/50" size={16} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') load(0, false).catch(() => undefined);
              }}
              className="min-w-0 flex-1 bg-transparent text-xs text-primary outline-none placeholder:text-primary/20"
              placeholder={t('favorites_search')}
              type="text"
            />
          </label>
          <button
            onClick={() => load(0, false)}
            className="flex h-10 items-center justify-center border border-primary/20 bg-black px-4 text-xs font-bold uppercase tracking-widest text-primary transition-colors hover:border-primary hover:bg-primary/5"
            type="button"
          >
            {t('favorites_search_action')}
          </button>
        </div>
      </div>

      {loading && items.length === 0 ? (
        <div className="flex min-h-[320px] items-center justify-center gap-3 border border-primary/20 bg-black/50 text-xs uppercase tracking-[0.3em] text-primary/70">
          <Loader2 className="animate-spin" size={16} />
          {t('favorites_loading')}
        </div>
      ) : items.length > 0 ? (
        <>
          <MasonryGrid
            items={items}
            getKey={(item: InspirationItem) => item.id}
            renderItem={(item: InspirationItem) => {
              const removing = removingIds.includes(item.id);
              return (
                <div className="overflow-hidden border border-secondary/25 bg-black transition-colors hover:border-secondary/50">
                  {item.image_url ? (
                    <button
                      className="block w-full cursor-zoom-in bg-black text-left"
                      type="button"
                      onClick={() => setPreviewItem(item)}
                    >
                      <img
                        alt={item.title}
                        className="block h-auto w-full opacity-95 transition-opacity duration-300 hover:opacity-100"
                        loading="lazy"
                        src={item.image_url}
                      />
                    </button>
                  ) : null}

                  <div className="border-t border-white/10 bg-surface-container-low/80 p-4">
                    <div className="mb-2 flex items-center justify-between gap-3">
                      <div className="min-w-0 truncate text-[10px] uppercase tracking-widest text-secondary">{item.title}</div>
                      <div className="shrink-0 text-[10px] text-white/30">{item.favorite_created_at ? formatDate(item.favorite_created_at) : item.section}</div>
                    </div>
                    <p className="mb-3 line-clamp-3 text-sm text-white/80">{item.prompt}</p>
                    <div className="grid grid-cols-[44px_44px_1fr] gap-2">
                      <button
                        className="flex h-10 items-center justify-center border border-white/10 bg-white/5 text-white/70 transition-colors hover:border-primary hover:text-primary disabled:opacity-40"
                        type="button"
                        onClick={() => setPreviewItem(item)}
                        disabled={!item.image_url}
                        title={t('history_preview')}
                      >
                        <Maximize2 size={15} />
                      </button>
                      <button
                        className="flex h-10 items-center justify-center border border-secondary/35 bg-secondary/10 text-secondary transition-colors hover:bg-secondary/20 disabled:cursor-not-allowed disabled:opacity-50"
                        type="button"
                        disabled={removing}
                        onClick={() => handleUnfavorite(item)}
                        title={t('home_unfavorite_case')}
                      >
                        {removing ? <Loader2 className="animate-spin" size={15} /> : <HeartOff size={15} />}
                      </button>
                      <button
                        className="flex h-10 min-w-0 items-center justify-center gap-2 bg-primary px-3 text-xs font-black uppercase text-black shadow-[0_0_10px_rgba(0,243,255,0.35)] transition-colors hover:bg-white"
                        type="button"
                        onClick={() => handleClonePrompt(item).catch(() => undefined)}
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

          <div className="mt-12 flex justify-center">
            {offset < total ? (
              <button
                onClick={() => load(offset, true)}
                disabled={loading}
                className="flex items-center gap-2 border border-primary/30 bg-primary/5 px-8 py-3 text-xs uppercase tracking-widest text-primary transition-colors hover:border-primary disabled:opacity-50"
                type="button"
              >
                {loading ? <Loader2 className="animate-spin" size={14} /> : <ArrowDown size={14} />}
                {t('history_load_more')}
              </button>
            ) : (
              <div className="text-xs uppercase tracking-[0.3em] text-white/35">{t('favorites_all_loaded')}</div>
            )}
          </div>
        </>
      ) : (
        <div className="flex min-h-[320px] items-center justify-center border border-primary/20 bg-black/50 px-6 text-center text-sm text-white/50">
          {t('favorites_empty')}
        </div>
      )}

      <ImagePreviewModal
        imageUrl={previewItem?.image_url || null}
        alt={previewItem?.title || 'preview'}
        subtitle={previewItem?.prompt}
        onClose={() => setPreviewItem(null)}
      />
    </div>
  );
}
