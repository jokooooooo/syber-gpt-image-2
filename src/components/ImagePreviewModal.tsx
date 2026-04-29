import { ExternalLink, X } from 'lucide-react';
import { useSite } from '../site';

type Props = {
  imageUrl: string | null;
  alt: string;
  subtitle?: string | null;
  onClose: () => void;
};

export default function ImagePreviewModal({ imageUrl, alt, subtitle, onClose }: Props) {
  const { t } = useSite();

  if (!imageUrl) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-[220] flex items-center justify-center bg-black/85 px-4 backdrop-blur-sm" onClick={onClose}>
      <div
        className="w-full max-w-6xl border border-primary/30 bg-surface-container-high shadow-[0_0_40px_rgba(0,243,255,0.18)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-4 border-b border-white/10 px-5 py-4">
          <div className="min-w-0">
            <div className="text-[10px] uppercase tracking-widest text-secondary">{t('modal_preview')}</div>
            {subtitle ? <div className="mt-1 truncate text-sm text-white/70">{subtitle}</div> : null}
          </div>
          <div className="flex items-center gap-2">
            <a
              className="flex h-10 items-center gap-2 border border-primary/30 px-4 text-xs font-bold uppercase tracking-widest text-primary transition-colors hover:bg-primary/10"
              href={imageUrl}
              rel="noreferrer"
              target="_blank"
            >
              <ExternalLink size={14} />
              {t('modal_open_image')}
            </a>
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

        <div className="flex max-h-[80vh] items-center justify-center overflow-auto bg-black p-4">
          <img alt={alt} className="max-h-[75vh] w-auto max-w-full object-contain" src={imageUrl} />
        </div>
      </div>
    </div>
  );
}
