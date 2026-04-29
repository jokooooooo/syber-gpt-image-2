import { CheckCircle2, Clock3, ImageIcon, Loader2, X, XCircle } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useMemo, useState } from 'react';
import { formatDate } from '../api';
import ImagePreviewModal from './ImagePreviewModal';
import { useSite } from '../site';
import { useTasks } from '../tasks';

type FilterKey = 'all' | 'active' | 'succeeded' | 'failed';

function statusLabel(status: 'queued' | 'running' | 'succeeded' | 'failed', t: ReturnType<typeof useSite>['t']) {
  if (status === 'queued') return t('tasks_status_queued');
  if (status === 'running') return t('tasks_status_running');
  if (status === 'succeeded') return t('tasks_status_succeeded');
  return t('tasks_status_failed');
}

function statusIcon(status: 'queued' | 'running' | 'succeeded' | 'failed') {
  if (status === 'queued') return <Clock3 size={14} className="text-white/70" />;
  if (status === 'running') return <Loader2 size={14} className="animate-spin text-primary" />;
  if (status === 'succeeded') return <CheckCircle2 size={14} className="text-secondary" />;
  return <XCircle size={14} className="text-error" />;
}

export default function TaskDrawer() {
  const { t } = useSite();
  const { tasks, drawerOpen, closeDrawer, activeCount } = useTasks();
  const [filter, setFilter] = useState<FilterKey>('all');
  const [previewItem, setPreviewItem] = useState<{ imageUrl: string; prompt: string } | null>(null);

  const visibleTasks = useMemo(() => {
    if (filter === 'all') {
      return tasks;
    }
    if (filter === 'active') {
      return tasks.filter((task) => task.status === 'queued' || task.status === 'running');
    }
    return tasks.filter((task) => task.status === filter);
  }, [filter, tasks]);

  return (
    <>
      <div
        className={`fixed inset-0 z-[120] bg-black/60 backdrop-blur-sm transition-opacity duration-300 ${
          drawerOpen ? 'pointer-events-auto opacity-100' : 'pointer-events-none opacity-0'
        }`}
        onClick={closeDrawer}
      />
      <aside
        className={`fixed right-0 top-0 z-[130] h-full w-full max-w-[420px] border-l border-primary/20 bg-surface-container-high/95 backdrop-blur-xl transition-transform duration-300 ${
          drawerOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        <div className="flex h-full flex-col">
          <div className="flex items-start justify-between border-b border-white/10 px-6 py-5">
            <div>
              <div className="mb-2 flex items-center gap-2 text-[10px] uppercase tracking-[0.25em] text-primary/70">
                <span className="h-1.5 w-1.5 rounded-full bg-primary" />
                {t('top_tasks')}
              </div>
              <h2 className="text-xl font-black tracking-tight text-white">{t('tasks_title')}</h2>
              <p className="mt-1 text-xs text-white/45">{t('tasks_subtitle')}</p>
            </div>
            <button
              className="flex h-10 w-10 items-center justify-center border border-white/10 text-white/70 transition-colors hover:border-primary hover:text-primary"
              type="button"
              onClick={closeDrawer}
              title={t('modal_close')}
            >
              <X size={16} />
            </button>
          </div>

          <div className="flex items-center justify-between border-b border-white/10 px-6 py-3 text-[11px] uppercase tracking-[0.22em] text-white/50">
            <span>{activeCount > 0 ? t('tasks_active', { value: activeCount }) : t('tasks_idle')}</span>
            <Link
              className="text-primary transition-colors hover:text-white"
              to="/history"
              onClick={closeDrawer}
            >
              {t('tasks_open_history')}
            </Link>
          </div>

          <div className="grid grid-cols-4 gap-2 border-b border-white/10 px-4 py-3">
            {([
              ['all', t('tasks_filter_all')],
              ['active', t('tasks_filter_active')],
              ['succeeded', t('tasks_filter_succeeded')],
              ['failed', t('tasks_filter_failed')],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                className={`border px-2 py-2 text-[10px] font-bold uppercase tracking-[0.16em] transition-colors ${
                  filter === key
                    ? 'border-primary bg-primary/12 text-primary'
                    : 'border-white/10 bg-black/20 text-white/50 hover:border-white/20 hover:text-white'
                }`}
                type="button"
                onClick={() => setFilter(key)}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto px-4 py-4">
            {visibleTasks.length === 0 ? (
              <div className="flex h-full min-h-[240px] items-center justify-center border border-dashed border-white/10 bg-black/20 px-6 text-center text-sm text-white/40">
                {t('tasks_empty')}
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                {visibleTasks.map((task) => {
                  const previewImage = task.items.find((item) => item.image_url)?.image_url || null;
                  return (
                    <div key={task.id} className="border border-white/10 bg-black/30 p-3">
                      <div className="mb-3 flex items-start justify-between gap-3">
                        <div className="flex min-w-0 items-center gap-2">
                          {statusIcon(task.status)}
                          <div className="min-w-0">
                            <div className="text-[11px] font-bold uppercase tracking-[0.2em] text-white/70">
                              {task.mode === 'edit' ? t('home_mode_edit') : t('home_mode_generate')}
                            </div>
                            <div className="mt-1 text-[11px] text-white/45">{formatDate(task.created_at)}</div>
                          </div>
                        </div>
                        <div className="border border-white/10 px-2 py-1 text-[10px] uppercase tracking-[0.18em] text-white/60">
                          {statusLabel(task.status, t)}
                        </div>
                      </div>

                      <div className="flex gap-3">
                        <div className="flex h-20 w-20 shrink-0 items-center justify-center overflow-hidden border border-white/10 bg-black/40">
                          {previewImage ? (
                            <button
                              className="h-full w-full cursor-zoom-in bg-black/40"
                              type="button"
                              title={t('history_preview')}
                              onClick={() => setPreviewItem({ imageUrl: previewImage, prompt: task.prompt })}
                            >
                              <img alt={task.prompt} className="h-full w-full object-contain" src={previewImage} />
                            </button>
                          ) : (
                            <ImageIcon size={18} className="text-white/25" />
                          )}
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="line-clamp-3 text-sm text-white/85">{task.prompt}</p>
                          <div className="mt-2 flex flex-wrap gap-2 text-[10px] uppercase tracking-[0.18em] text-white/45">
                            <span>{task.model}</span>
                            <span>{task.size}</span>
                            {task.aspect_ratio ? <span>{task.aspect_ratio}</span> : null}
                            <span>{task.quality}</span>
                          </div>
                          {task.error ? <div className="mt-2 text-xs text-error">{task.error}</div> : null}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </aside>
      <ImagePreviewModal
        imageUrl={previewItem?.imageUrl || null}
        alt={previewItem?.prompt || 'preview'}
        subtitle={previewItem?.prompt}
        onClose={() => setPreviewItem(null)}
      />
    </>
  );
}
