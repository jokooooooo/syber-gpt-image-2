import { Clipboard, Minimize2, Trash2, X } from 'lucide-react';
import { useSite } from '../site';

type Props = {
  open: boolean;
  value: string;
  onChange: (value: string) => void;
  onClose: () => void;
  onCopy: () => void;
};

export default function PromptEditorModal({ open, value, onChange, onClose, onCopy }: Props) {
  const { t } = useSite();

  if (!open) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-[220] flex items-center justify-center bg-black/85 px-3 py-4 backdrop-blur-sm sm:px-6" onClick={onClose}>
      <div
        className="flex h-[calc(100vh-2rem)] w-full max-w-5xl flex-col border border-primary/30 bg-surface-container-high shadow-[0_0_40px_rgba(0,243,255,0.18)] sm:h-[82vh]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 border-b border-white/10 px-4 py-3 sm:px-5">
          <div className="min-w-0">
            <div className="text-[10px] uppercase tracking-widest text-secondary">{t('prompt_editor_title')}</div>
            <div className="mt-1 truncate font-code-data text-[10px] uppercase tracking-[0.18em] text-primary/45">
              UTF-8 // AI-GEN // [{value.length}/8000]
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              className="flex h-10 items-center gap-2 border border-primary/30 px-3 text-xs font-bold uppercase tracking-widest text-primary transition-colors hover:bg-primary/10"
              type="button"
              onClick={onCopy}
              title={t('prompt_editor_copy')}
            >
              <Clipboard size={14} />
              <span className="hidden sm:inline">{t('prompt_editor_copy')}</span>
            </button>
            <button
              className="flex h-10 w-10 items-center justify-center border border-white/10 text-white/60 transition-colors hover:border-error hover:text-error"
              type="button"
              onClick={() => onChange('')}
              title={t('prompt_editor_clear')}
            >
              <Trash2 size={15} />
            </button>
            <button
              className="hidden h-10 items-center gap-2 border border-secondary/30 px-3 text-xs font-bold uppercase tracking-widest text-secondary transition-colors hover:bg-secondary/10 sm:flex"
              type="button"
              onClick={onClose}
              title={t('prompt_editor_collapse')}
            >
              <Minimize2 size={14} />
              {t('prompt_editor_collapse')}
            </button>
            <button
              className="flex h-10 w-10 items-center justify-center border border-white/10 text-white/60 transition-colors hover:border-primary hover:text-primary"
              type="button"
              onClick={onClose}
              title={t('modal_close')}
            >
              <X size={16} />
            </button>
          </div>
        </div>

        <textarea
          autoFocus
          className="min-h-0 flex-1 resize-none bg-black p-4 text-sm leading-6 text-primary outline-none placeholder:text-primary/20 sm:p-5"
          maxLength={8000}
          placeholder={t('home_placeholder')}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
    </div>
  );
}
