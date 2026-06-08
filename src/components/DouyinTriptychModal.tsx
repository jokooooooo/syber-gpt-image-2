import { Archive, Download, ExternalLink, X } from 'lucide-react';
import { douyinTriptychPartDownloadUrl, douyinTriptychZipDownloadUrl, HistoryItem } from '../api';
import {
  DOUYIN_TRIPTYCH_EXPORT_SIZE,
  DOUYIN_TRIPTYCH_TILE_SIZE,
} from '../douyinTriptych';
import { useSite } from '../site';
import RetryImage from './RetryImage';

type Props = {
  item: HistoryItem | null;
  onClose: () => void;
};

const PARTS = [
  { key: 'left', labelKey: 'douyin_triptych_left', order: 3 },
  { key: 'center', labelKey: 'douyin_triptych_center', order: 2 },
  { key: 'right', labelKey: 'douyin_triptych_right', order: 1 },
] as const;

export default function DouyinTriptychModal({ item, onClose }: Props) {
  const { t } = useSite();
  if (!item?.image_url) return null;

  return (
    <div className="fixed inset-0 z-[230] flex items-center justify-center bg-black/85 px-3 py-4 backdrop-blur-sm" onClick={onClose}>
      <div
        className="flex max-h-[92vh] w-full max-w-6xl flex-col border border-secondary/35 bg-surface-container-high shadow-[0_0_44px_rgba(255,0,255,0.18)]"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-white/10 px-4 py-4 md:px-5">
          <div className="min-w-0">
            <div className="text-[10px] font-bold uppercase tracking-widest text-secondary">{t('douyin_triptych_tag')}</div>
            <h2 className="mt-1 text-lg font-black tracking-tight text-white">{t('douyin_triptych_title')}</h2>
            <p className="mt-1 text-xs text-white/50">
              {t('douyin_triptych_size_hint', { full: DOUYIN_TRIPTYCH_EXPORT_SIZE, tile: DOUYIN_TRIPTYCH_TILE_SIZE })}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <a
              className="hidden h-10 items-center gap-2 border border-secondary/35 bg-secondary/10 px-3 text-[10px] font-black uppercase tracking-widest text-secondary transition-colors hover:bg-secondary hover:text-black sm:flex"
              href={douyinTriptychZipDownloadUrl(item.id)}
            >
              <Archive size={14} />
              {t('history_download_zip')}
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

        <div className="overflow-auto px-4 py-4 md:px-5">
          <div className="mb-4 overflow-hidden border border-primary/25 bg-black">
            <div className="relative aspect-[9/4] w-full">
              <RetryImage alt={item.prompt} className="h-full w-full object-cover" src={item.image_url} />
              <div className="pointer-events-none absolute bottom-0 top-0 left-1/3 w-px bg-secondary/80 shadow-[0_0_12px_rgba(255,0,255,0.9)]" />
              <div className="pointer-events-none absolute bottom-0 top-0 left-2/3 w-px bg-secondary/80 shadow-[0_0_12px_rgba(255,0,255,0.9)]" />
            </div>
          </div>

          <div className="mb-4 grid gap-3 md:grid-cols-3">
            {PARTS.map((part, index) => (
              <div key={part.key} className="border border-white/10 bg-black/60 p-2">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <div>
                    <div className="text-[10px] font-bold uppercase tracking-widest text-primary">{t(part.labelKey)}</div>
                    <div className="mt-0.5 text-[10px] text-white/40">{t('douyin_triptych_post_order', { value: part.order })}</div>
                  </div>
                  <a
                    className="flex h-8 w-8 items-center justify-center border border-white/10 text-white/65 transition-colors hover:border-primary hover:text-primary"
                    href={douyinTriptychPartDownloadUrl(item.id, part.key)}
                    title={t('modal_download')}
                  >
                    <Download size={13} />
                  </a>
                </div>
                <div className="aspect-[3/4] overflow-hidden bg-black">
                  <RetryImage
                    alt={`${item.id}-${index + 1}`}
                    className="h-full w-full object-cover"
                    src={douyinTriptychPartDownloadUrl(item.id, part.key)}
                  />
                </div>
              </div>
            ))}
          </div>

          <div className="grid gap-3 border border-white/10 bg-black/40 p-3 text-xs text-white/55 md:grid-cols-[1fr_auto] md:items-center">
            <div>
              <div className="mb-1 font-bold text-white/80">{t('douyin_triptych_publish_tip_title')}</div>
              <div>{t('douyin_triptych_publish_tip_body')}</div>
            </div>
            <div className="flex gap-2">
              <a
                className="flex h-10 items-center justify-center gap-2 border border-white/10 px-3 text-[10px] font-bold uppercase tracking-widest text-white/70 transition-colors hover:border-primary hover:text-primary"
                href={item.image_url}
                rel="noreferrer"
                target="_blank"
              >
                <ExternalLink size={13} />
                {t('modal_open_image')}
              </a>
              <a
                className="flex h-10 items-center justify-center gap-2 bg-secondary px-3 text-[10px] font-black uppercase tracking-widest text-black transition-colors hover:bg-white"
                href={douyinTriptychZipDownloadUrl(item.id)}
              >
                <Archive size={13} />
                {t('history_download_zip')}
              </a>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
