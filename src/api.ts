const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

export type ViewerInfo = {
  authenticated: boolean;
  owner_id: string;
  guest_id: string;
  api_key_source: 'managed' | 'manual' | 'manual_override';
  user: {
    id: number;
    email: string;
    username: string;
    role: string;
  } | null;
};

export type AppConfig = {
  owner_id: string;
  model: string;
  default_size: string;
  default_quality: string;
  user_name: string;
  managed_by_auth: boolean;
  api_key_set: boolean;
  api_key_hint: string;
  api_key_source: 'managed' | 'manual' | 'manual_override';
  api_key_editable: boolean;
  authenticated: boolean;
};

export type HistoryItem = {
  id: string;
  owner_id: string;
  task_id: string | null;
  batch_index: number;
  mode: 'generate' | 'edit';
  prompt: string;
  model: string;
  size: string;
  aspect_ratio: string;
  quality: string;
  status: 'succeeded' | 'failed';
  image_url: string | null;
  image_path: string | null;
  input_image_url: string | null;
  input_image_path: string | null;
  revised_prompt: string | null;
  usage: Record<string, unknown> | null;
  provider_response: Record<string, unknown> | null;
  task_prompt: string | null;
  task_result: {
    ecommerce_analysis?: Record<string, unknown> | null;
    series_plan?: {
      source?: string;
      style_guide?: string;
      items?: { index: number; title: string; copy: string; prompt: string }[];
    };
    [key: string]: unknown;
  } | null;
  task_request: {
    ecommerce?: {
      product_name: string;
      materials: string;
      selling_points: string;
      scenarios: string;
      platform: string;
      style: string;
      extra_requirements: string;
      analysis?: Record<string, unknown> | null;
    };
  } | null;
  error: string | null;
  published: boolean;
  published_inspiration_id: string | null;
  published_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ImageTask = {
  id: string;
  owner_id: string;
  mode: 'generate' | 'edit';
  prompt: string;
  model: string;
  size: string;
  aspect_ratio: string;
  quality: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed';
  error: string | null;
  items: HistoryItem[];
  result: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
};

export type InspirationItem = {
  id: string;
  source_url: string;
  source_item_id: string;
  section: string;
  title: string;
  author: string | null;
  prompt: string;
  image_url: string | null;
  source_link: string | null;
  favorited: boolean;
  favorite_created_at: string | null;
  synced_at: string;
  created_at: string;
  updated_at: string;
};

export type InspirationStats = {
  total: number;
  last_synced_at: string | null;
  sections: number;
  section_counts: { section: string; count: number }[];
  source_url: string;
  source_urls: string[];
  source_counts: { source_url: string; count: number; last_synced_at: string | null }[];
  sync_interval_seconds: number;
  last_error: string | null;
};

export type InspirationListResponse = {
  items: InspirationItem[];
  total: number;
  limit: number;
  offset: number;
};

export type BalanceInfo = {
  ok: boolean;
  remaining: number | null;
  message?: string;
  raw: Record<string, unknown> | null;
};

export type AccountInfo = {
  viewer: ViewerInfo;
  user: {
    name: string;
    email: string | null;
    username: string | null;
    role: string | null;
    authenticated: boolean;
    guest: boolean;
    api_key_set: boolean;
    api_key_source: 'managed' | 'manual' | 'manual_override';
    model: string;
  };
  balance: BalanceInfo;
  stats: {
    total: number;
    succeeded: number;
    edits: number;
    last_generation_at: string | null;
  };
};

export type LedgerEntry = {
  id: string;
  owner_id: string;
  event_type: string;
  amount: number;
  currency: string;
  description: string;
  history_id: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
};

export type GeneratePayload = {
  prompt: string;
  model?: string;
  size?: string;
  aspect_ratio?: string;
  quality?: string;
  n?: number;
};

export type PromptOptimizePayload = {
  prompt: string;
  instruction?: string;
  model?: string;
  size?: string;
  aspect_ratio?: string;
  quality?: string;
};

export type PromptOptimizeResult = {
  prompt: string;
  original_prompt: string;
  instruction: string;
  model: string;
  usage: Record<string, unknown> | null;
};

export type EcommercePublishCopyPayload = {
  product_name?: string;
  materials?: string;
  selling_points?: string;
  scenarios?: string;
  platform?: string;
  style?: string;
  extra_requirements?: string;
  image_count?: number;
  size?: string;
  aspect_ratio?: string;
  model?: string;
};

export type EcommercePublishCopyResult = {
  title: string;
  body: string;
  model: string;
  usage: Record<string, unknown> | null;
};

export type EcommerceGeneratePayload = {
  product_name?: string;
  materials?: string;
  selling_points?: string;
  scenarios?: string;
  platform?: string;
  style?: string;
  extra_requirements?: string;
  model?: string;
  size?: string;
  aspect_ratio?: string;
  quality?: string;
  n?: number;
};

export type PublicAuthSettings = {
  registration_enabled: boolean;
  email_verify_enabled: boolean;
  force_email_on_third_party_signup: boolean;
  promo_code_enabled: boolean;
  invitation_code_enabled: boolean;
  totp_enabled: boolean;
  turnstile_enabled: boolean;
  turnstile_site_key: string;
  backend_mode_enabled: boolean;
  site_name: string;
  site_subtitle: string;
};

export type SiteSettings = {
  default_locale: 'zh-CN' | 'en-US' | string;
  announcement: {
    enabled: boolean;
    title: string;
    body: string;
    updated_at: string | null;
  };
  inspiration_sources: string[];
  upstream?: {
    provider_base_url: string;
    auth_base_url: string;
    effective_provider_base_url: string;
    effective_auth_base_url: string;
  };
  viewer: {
    authenticated: boolean;
    is_admin: boolean;
  };
};

export type LoginResult = {
  ok: boolean;
  viewer?: ViewerInfo;
  requires_2fa?: boolean;
  temp_token?: string;
  user_email_masked?: string;
};

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    ...options,
    headers: {
      ...(options?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(options?.headers || {}),
    },
  });
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const detail = data?.detail || data?.message || response.statusText;
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data as T;
}

export function getSession() {
  return request<ViewerInfo>('/api/auth/session');
}

export function getSiteSettings() {
  return request<SiteSettings>('/api/site-settings');
}

export function updateSiteSettings(payload: {
  default_locale?: 'zh-CN' | 'en-US';
  announcement_enabled?: boolean;
  announcement_title?: string;
  announcement_body?: string;
  inspiration_sources?: string[];
  provider_base_url?: string;
  auth_base_url?: string;
}) {
  return request<SiteSettings>('/api/site-settings', {
    method: 'PUT',
    body: JSON.stringify(payload),
  });
}

export function getAuthPublicSettings() {
  return request<PublicAuthSettings>('/api/auth/public-settings');
}

export function sendVerifyCode(payload: { email: string; turnstile_token?: string }) {
  return request<{ message: string; countdown: number }>('/api/auth/send-verify-code', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function registerAccount(payload: {
  email: string;
  password: string;
  verify_code?: string;
  turnstile_token?: string;
  promo_code?: string;
  invitation_code?: string;
}) {
  return request<LoginResult>('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function loginAccount(payload: { email: string; password: string; turnstile_token?: string }) {
  return request<LoginResult>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function loginAccount2FA(payload: { temp_token: string; totp_code: string }) {
  return request<LoginResult>('/api/auth/login/2fa', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function logoutAccount() {
  return request<{ ok: boolean }>('/api/auth/logout', {
    method: 'POST',
    body: JSON.stringify({}),
  });
}

export function getConfig() {
  return request<AppConfig>('/api/config');
}

export function saveConfig(config: Partial<AppConfig> & { api_key?: string; clear_api_key?: boolean }) {
  return request<AppConfig>('/api/config', {
    method: 'PUT',
    body: JSON.stringify(config),
  });
}

export function testConfig() {
  return request<{ ok: boolean; models: string[] }>('/api/config/test', { method: 'POST' });
}

export function getAccount() {
  return request<AccountInfo>('/api/account');
}

export function getBalance() {
  return request<BalanceInfo>('/api/balance');
}

export function getLedger(limit = 20) {
  return request<{ items: LedgerEntry[] }>(`/api/ledger?limit=${limit}`);
}

export function getHistory(params: { limit?: number; offset?: number; q?: string } = {}) {
  const search = new URLSearchParams();
  if (params.limit) search.set('limit', String(params.limit));
  if (params.offset) search.set('offset', String(params.offset));
  if (params.q) search.set('q', params.q);
  const query = search.toString();
  return request<{ items: HistoryItem[] }>(`/api/history${query ? `?${query}` : ''}`);
}

export function getInspirations(params: { limit?: number; offset?: number; q?: string; section?: string } = {}) {
  const search = new URLSearchParams();
  if (params.limit) search.set('limit', String(params.limit));
  if (params.offset) search.set('offset', String(params.offset));
  if (params.q) search.set('q', params.q);
  if (params.section) search.set('section', params.section);
  const query = search.toString();
  return request<InspirationListResponse>(`/api/inspirations${query ? `?${query}` : ''}`);
}

export function getFavoriteInspirations(params: { limit?: number; offset?: number; q?: string; section?: string } = {}) {
  const search = new URLSearchParams();
  if (params.limit) search.set('limit', String(params.limit));
  if (params.offset) search.set('offset', String(params.offset));
  if (params.q) search.set('q', params.q);
  if (params.section) search.set('section', params.section);
  const query = search.toString();
  return request<InspirationListResponse>(`/api/inspirations/favorites${query ? `?${query}` : ''}`);
}

export function favoriteInspiration(id: string) {
  return request<{ ok: boolean; item: InspirationItem }>(`/api/inspirations/${id}/favorite`, { method: 'POST' });
}

export function unfavoriteInspiration(id: string) {
  return request<{ ok: boolean; item: InspirationItem }>(`/api/inspirations/${id}/favorite`, { method: 'DELETE' });
}

export function getInspirationStats() {
  return request<InspirationStats>('/api/inspirations/stats');
}

export function syncInspirations() {
  return request<{
    ok: boolean;
    parsed: number;
    count: number;
    cached_images: number;
    synced_at: string;
    source_urls: string[];
    image_cache_errors: { url: string; error: string }[];
  }>('/api/inspirations/sync', {
    method: 'POST',
  });
}

export function deleteHistory(id: string) {
  return request<{ ok: boolean }>(`/api/history/${id}`, { method: 'DELETE' });
}

export function publishHistory(id: string) {
  return request<{ ok: boolean; item: HistoryItem; inspiration: InspirationItem }>(`/api/history/${id}/publish`, {
    method: 'POST',
  });
}

export function unpublishHistory(id: string) {
  return request<{ ok: boolean; item: HistoryItem }>(`/api/history/${id}/publish`, { method: 'DELETE' });
}

export function editHistoryImage(id: string, payload: GeneratePayload, referenceImages: File[] = []) {
  if (referenceImages.length === 0) {
    return request<ImageTask>(`/api/history/${id}/edit`, {
      method: 'POST',
      body: JSON.stringify(payload),
    });
  }
  const form = new FormData();
  form.set('prompt', payload.prompt);
  if (payload.model) form.set('model', payload.model);
  if (payload.size) form.set('size', payload.size);
  if (payload.aspect_ratio) form.set('aspect_ratio', payload.aspect_ratio);
  if (payload.quality) form.set('quality', payload.quality);
  referenceImages.forEach((image) => form.append('image', image));
  return request<ImageTask>(`/api/history/${id}/edit`, {
    method: 'POST',
    body: form,
  });
}

export function generateImage(payload: GeneratePayload) {
  return request<ImageTask>('/api/images/generate', {
    method: 'POST',
    body: JSON.stringify({ ...payload, n: payload.n || 1 }),
  });
}

export function optimizePrompt(payload: PromptOptimizePayload) {
  return request<PromptOptimizeResult>('/api/prompts/optimize', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function generateEcommercePublishCopy(payload: EcommercePublishCopyPayload) {
  return request<EcommercePublishCopyResult>('/api/ecommerce/publish-copy', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function editImage(payload: GeneratePayload, images: File | File[]) {
  const form = new FormData();
  const imageList = Array.isArray(images) ? images : [images];
  form.set('prompt', payload.prompt);
  if (payload.model) form.set('model', payload.model);
  if (payload.size) form.set('size', payload.size);
  if (payload.aspect_ratio) form.set('aspect_ratio', payload.aspect_ratio);
  if (payload.quality) form.set('quality', payload.quality);
  form.set('n', String(payload.n || 1));
  imageList.forEach((image) => form.append('image', image));
  return request<ImageTask>('/api/images/edit', {
    method: 'POST',
    body: form,
  });
}

export function generateEcommerceImages(payload: EcommerceGeneratePayload, image: File) {
  const form = new FormData();
  form.set('image', image);
  if (payload.product_name) form.set('product_name', payload.product_name);
  if (payload.materials) form.set('materials', payload.materials);
  if (payload.selling_points) form.set('selling_points', payload.selling_points);
  if (payload.scenarios) form.set('scenarios', payload.scenarios);
  if (payload.platform) form.set('platform', payload.platform);
  if (payload.style) form.set('style', payload.style);
  if (payload.extra_requirements) form.set('extra_requirements', payload.extra_requirements);
  if (payload.model) form.set('model', payload.model);
  if (payload.size) form.set('size', payload.size);
  if (payload.aspect_ratio) form.set('aspect_ratio', payload.aspect_ratio);
  if (payload.quality) form.set('quality', payload.quality);
  form.set('n', String(payload.n || 4));
  return request<ImageTask>('/api/ecommerce/generate', {
    method: 'POST',
    body: form,
  });
}

export function getImageTask(taskId: string) {
  return request<ImageTask>(`/api/tasks/${taskId}`);
}

export function listImageTasks(params: { limit?: number; status?: string[] } = {}) {
  const search = new URLSearchParams();
  if (params.limit) search.set('limit', String(params.limit));
  if (params.status && params.status.length > 0) search.set('status', params.status.join(','));
  const query = search.toString();
  return request<{ items: ImageTask[] }>(`/api/tasks${query ? `?${query}` : ''}`);
}

export async function waitForImageTask(
  taskId: string,
  options: {
    intervalMs?: number;
    timeoutMs?: number;
    onUpdate?: (task: ImageTask) => void;
  } = {},
) {
  const intervalMs = options.intervalMs ?? 1500;
  const timeoutMs = options.timeoutMs ?? 15 * 60 * 1000;
  const startedAt = Date.now();

  while (true) {
    const task = await getImageTask(taskId);
    options.onUpdate?.(task);
    if (task.status === 'succeeded' || task.status === 'failed') {
      return task;
    }
    if (Date.now() - startedAt >= timeoutMs) {
      throw new Error('Image task polling timed out');
    }
    await new Promise((resolve) => window.setTimeout(resolve, intervalMs));
  }
}

export function formatBalance(balance: BalanceInfo | undefined) {
  if (!balance || balance.remaining === null || Number.isNaN(balance.remaining)) {
    return '--';
  }
  return balance.remaining.toFixed(4);
}

export function formatDate(value: string | null | undefined) {
  if (!value) return '--';
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value));
}
