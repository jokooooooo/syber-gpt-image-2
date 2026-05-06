import { CheckCircle2, Info, X, XCircle } from 'lucide-react';
import { useSite } from '../site';
import { useTasks } from '../tasks';
import type { NoticeToast } from '../tasks';

export default function TaskToastStack() {
  const { t } = useSite();
  const { toasts, dismissToast } = useTasks();

  if (toasts.length === 0) {
    return null;
  }

  return (
    <div className="pointer-events-none fixed right-6 top-20 z-[160] flex w-[min(92vw,380px)] flex-col gap-3">
      {toasts.map((toast) => {
        const isTaskToast = toast.type === 'task';
        const kind: NoticeToast['kind'] = isTaskToast ? (toast.status === 'succeeded' ? 'success' : 'error') : toast.kind;
        const succeeded = kind === 'success';
        const errored = kind === 'error';
        const title = isTaskToast
          ? succeeded
            ? t('tasks_toast_succeeded')
            : t('tasks_toast_failed')
          : toast.title || (errored ? t('toast_error') : succeeded ? t('toast_success') : t('toast_info'));
        const body = isTaskToast ? toast.prompt : toast.message;
        return (
          <div
            key={toast.id}
            className={`pointer-events-auto border p-4 shadow-[0_16px_40px_rgba(0,0,0,0.55)] backdrop-blur-xl ${
              succeeded
                ? 'border-secondary/40 bg-secondary/10'
                : errored
                  ? 'border-error/40 bg-error/10'
                  : 'border-primary/40 bg-primary/10'
            }`}
          >
            <div className="flex items-start gap-3">
              <div className="mt-0.5 shrink-0">
                {succeeded ? (
                  <CheckCircle2 size={18} className="text-secondary" />
                ) : errored ? (
                  <XCircle size={18} className="text-error" />
                ) : (
                  <Info size={18} className="text-primary" />
                )}
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-[11px] font-bold uppercase tracking-[0.2em] text-white/60">
                  {title}
                </div>
                <p className="mt-1 line-clamp-3 text-sm text-white/85">{body}</p>
                {isTaskToast && toast.error ? <div className="mt-2 text-xs text-error">{toast.error}</div> : null}
              </div>
              <button
                className="flex h-8 w-8 items-center justify-center border border-white/10 text-white/55 transition-colors hover:border-white/25 hover:text-white"
                type="button"
                onClick={() => dismissToast(toast.id)}
                title={t('modal_close')}
              >
                <X size={14} />
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
