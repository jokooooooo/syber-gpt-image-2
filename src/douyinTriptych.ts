import type { HistoryItem } from './api';

export const DOUYIN_TRIPTYCH_PRESET = 'douyin_triptych';
export const DOUYIN_TRIPTYCH_PROVIDER_SIZE = '3456x1536';
export const DOUYIN_TRIPTYCH_PROVIDER_RATIO = '9:4';
export const DOUYIN_TRIPTYCH_EXPORT_SIZE = '3240x1440';
export const DOUYIN_TRIPTYCH_TILE_SIZE = '1080x1440';

export function isDouyinTriptychItem(item: HistoryItem | null | undefined) {
  return item?.task_request?.layout_preset === DOUYIN_TRIPTYCH_PRESET;
}
